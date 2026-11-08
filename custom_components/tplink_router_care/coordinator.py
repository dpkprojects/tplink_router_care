"""Data update coordinator for TP-Link Router Care."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    GuestWifi,
    RouterApi,
    RouterAuthError,
    RouterData,
    RouterError,
    RouterLoginError,
)
from .const import (
    DATA_GUEST_SUPPORTED,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GUEST_RECHECK_DELAY,
    LOGIN_REFUSALS_BEFORE_REAUTH,
    POLL_RETRY_DELAYS,
    UPDATE_TIMEOUT,
)
from .rotation import GuestPasswordRotator

_LOGGER = logging.getLogger(__name__)

# hass.data key: refused logins in a row, per entry. Kept outside the
# coordinator so the count survives HA's setup retries, which build a new one.
_LOGIN_REFUSALS: str = f"{DOMAIN}_login_refusals"

_LOGIN_REFUSED_HINT = (
    "Router refused the login. Is someone logged into its web page? "
    "If this keeps happening, check the password"
)

type RouterConfigEntry = ConfigEntry[RouterCoordinator]


def scan_interval(entry: ConfigEntry) -> timedelta | None:
    """Polling interval from the options; None means automatic polling is off."""
    try:
        minutes = int(entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    except (TypeError, ValueError):
        minutes = int(DEFAULT_SCAN_INTERVAL)
    return timedelta(minutes=minutes) if minutes > 0 else None


class RouterCoordinator(DataUpdateCoordinator[RouterData]):
    """Polls the router in a worker thread so the event loop is never blocked."""

    config_entry: RouterConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: RouterConfigEntry, api: RouterApi
    ) -> None:
        # The interval chosen in the options. update_interval can be shorter
        # for a while, when retrying after a failed poll.
        self._configured_interval = scan_interval(entry)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=self._configured_interval,
        )
        self.api = api
        self._poll_failures = 0
        self._auth_failed = False
        self._recheck_unsub: CALLBACK_TYPE | None = None
        self.rotator = GuestPasswordRotator(self)
        entry.async_on_unload(self._async_cancel_recheck)

    # ------------------------------------------------------------------ #
    # State other parts of the integration look at
    # ------------------------------------------------------------------ #

    @property
    def auth_failed(self) -> bool:
        """True while HA is waiting for the user to re-enter the password."""
        return self._auth_failed

    @property
    def guest_supported(self) -> bool:
        """True if this router has a 5 GHz guest network.

        Remembered in the entry once seen, so a glitch while reading it during
        startup doesn't hide the guest entities until the next restart.
        """
        if self.data is not None and self.data.guest_5g is not None:
            return True
        return bool(self.config_entry.data.get(DATA_GUEST_SUPPORTED))

    # ------------------------------------------------------------------ #
    # Polling interval and retries
    # ------------------------------------------------------------------ #

    def _effective_interval(self) -> timedelta | None:
        """The configured interval, or a shorter retry wait after failed polls."""
        base = self._configured_interval
        if base is None or self._poll_failures == 0:
            # With polling disabled, failed manual refreshes aren't retried.
            return base
        step = min(self._poll_failures, len(POLL_RETRY_DELAYS)) - 1
        return min(base, POLL_RETRY_DELAYS[step])

    @callback
    def async_apply_scan_interval(self) -> None:
        """Follow the polling interval option without a reload or router login."""
        interval = scan_interval(self.config_entry)
        if interval == self._configured_interval:
            return
        self._configured_interval = interval
        self.update_interval = self._effective_interval()
        # Re-arm (or cancel) the timer now rather than after the next poll, so
        # e.g. going from 24 h to 30 min takes effect right away.
        self._async_unsub_refresh()
        self._schedule_refresh()

    @callback
    def _schedule_refresh(self) -> None:
        # No automatic logins while waiting for a new password: they would
        # only fail again and could get HA locked out of the router.
        if self._auth_failed:
            return
        super()._schedule_refresh()

    # ------------------------------------------------------------------ #
    # Refused logins
    # ------------------------------------------------------------------ #

    def _login_refusals(self) -> dict[str, int]:
        return self.hass.data.setdefault(_LOGIN_REFUSALS, {})

    @callback
    def _note_login_refused(self) -> bool:
        """Count a refused login; True once it's time to ask for a new password."""
        counts = self._login_refusals()
        entry_id = self.config_entry.entry_id
        counts[entry_id] = counts.get(entry_id, 0) + 1
        _LOGGER.debug(
            "Router refused login %s of %s", counts[entry_id], LOGIN_REFUSALS_BEFORE_REAUTH
        )
        if counts[entry_id] < LOGIN_REFUSALS_BEFORE_REAUTH:
            return False
        del counts[entry_id]
        self._auth_failed = True
        return True

    @callback
    def _note_login_ok(self) -> None:
        self._login_refusals().pop(self.config_entry.entry_id, None)
        self._auth_failed = False

    @callback
    def _async_start_reauth(self) -> None:
        self._auth_failed = True
        self._async_unsub_refresh()
        self.config_entry.async_start_reauth(self.hass)

    # ------------------------------------------------------------------ #
    # Polling
    # ------------------------------------------------------------------ #

    async def _async_update_data(self) -> RouterData:
        try:
            data = await self._async_fetch()
        except UpdateFailed:
            self._poll_failures += 1
            # Read by HA right after this returns, to schedule the next poll.
            self.update_interval = self._effective_interval()
            raise
        self._poll_failures = 0
        self.update_interval = self._effective_interval()
        return data

    async def _async_fetch(self) -> RouterData:
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                data = await self.hass.async_add_executor_job(self.api.fetch)
        except RouterAuthError as err:
            # Stops polling and asks the user for a new password.
            self._auth_failed = True
            raise ConfigEntryAuthFailed(str(err)) from err
        except RouterLoginError as err:
            if self._note_login_refused():
                raise ConfigEntryAuthFailed(
                    f"Router refused {LOGIN_REFUSALS_BEFORE_REAUTH} logins in a row; "
                    f"check the password. Last error: {err}"
                ) from err
            raise UpdateFailed(f"{_LOGIN_REFUSED_HINT}: {err}") from err
        except TimeoutError as err:
            raise UpdateFailed(f"Timed out talking to router: {err}") from err
        except RouterError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001 - never let library bugs escape
            _LOGGER.exception("Unexpected error polling router")
            raise UpdateFailed(f"Unexpected error: {err}") from err

        self._note_login_ok()
        data.fetched_at = dt_util.utcnow()
        if data.guest_5g is None and data.guest_5g_failed and self.data is not None:
            # A network glitch while reading the guest network: keep showing
            # the last known values rather than blanking the QR code until the
            # next poll (which may be a day or a week away).
            data.guest_5g = self.data.guest_5g
        if data.guest_5g is not None and not self.config_entry.data.get(
            DATA_GUEST_SUPPORTED
        ):
            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={**self.config_entry.data, DATA_GUEST_SUPPORTED: True},
            )
        return data

    # ------------------------------------------------------------------ #
    # Guest Wi-Fi changes
    # ------------------------------------------------------------------ #

    async def async_set_guest_5g(
        self, func: Callable[..., GuestWifi], *args: Any
    ) -> None:
        """Run a guest Wi-Fi change in the executor and publish the new state.

        Updates entities immediately from the router's read-back, without
        waiting for the next scheduled poll. If the change fails or times out,
        the router may have applied it anyway (in full or in part), so it is
        re-read shortly afterwards; otherwise the QR code could keep showing a
        password the router no longer uses.
        """
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                guest = await self.hass.async_add_executor_job(func, *args)
        except RouterAuthError as err:
            self._async_start_reauth()
            raise HomeAssistantError(f"Router rejected the password: {err}") from err
        except RouterLoginError as err:
            # Nothing was written, so no need to re-read.
            if self._note_login_refused():
                self._async_start_reauth()
            raise HomeAssistantError(f"{_LOGIN_REFUSED_HINT}: {err}") from err
        except TimeoutError as err:
            self._async_schedule_recheck()
            raise HomeAssistantError(
                "Timed out talking to router; will re-read it in a minute"
            ) from err
        except RouterError as err:
            self._async_schedule_recheck()
            raise HomeAssistantError(str(err)) from err
        except Exception as err:  # noqa: BLE001 - never let library bugs escape
            _LOGGER.exception("Unexpected error changing guest Wi-Fi")
            self._async_schedule_recheck()
            raise HomeAssistantError(f"Unexpected error: {err}") from err

        self._note_login_ok()
        self._async_cancel_recheck()
        if self.data is not None:
            self.async_set_updated_data(replace(self.data, guest_5g=guest))

    @callback
    def _async_schedule_recheck(self) -> None:
        self._async_cancel_recheck()
        self._recheck_unsub = async_call_later(
            self.hass, GUEST_RECHECK_DELAY, self._async_recheck
        )

    @callback
    def _async_cancel_recheck(self) -> None:
        if self._recheck_unsub:
            self._recheck_unsub()
            self._recheck_unsub = None

    async def _async_recheck(self, _now: datetime) -> None:
        self._recheck_unsub = None
        if not self._auth_failed:
            await self.async_refresh()
