"""Data update coordinator for TP-Link Router Care."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_SCAN_INTERVAL
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import GuestWifi, RouterApi, RouterAuthError, RouterData, RouterError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, UPDATE_TIMEOUT

_LOGGER = logging.getLogger(__name__)

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
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=scan_interval(entry),
        )
        self.api = api

    @callback
    def async_apply_scan_interval(self) -> None:
        """Follow the polling interval option without a reload or router login."""
        interval = scan_interval(self.config_entry)
        if interval == self.update_interval:
            return
        self.update_interval = interval
        # Re-arm (or cancel) the timer now rather than after the next poll, so
        # e.g. going from 24 h to 30 min takes effect right away. Stay stopped
        # after a rejected password until the user re-authenticates.
        self._async_unsub_refresh()
        if not isinstance(self.last_exception, ConfigEntryAuthFailed):
            self._schedule_refresh()

    async def _async_update_data(self) -> RouterData:
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                data = await self.hass.async_add_executor_job(self.api.fetch)
        except RouterAuthError as err:
            # Stops polling and asks the user for a new password.
            raise ConfigEntryAuthFailed(str(err)) from err
        except TimeoutError as err:
            raise UpdateFailed(f"Timed out talking to router: {err}") from err
        except RouterError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001 - never let library bugs escape
            _LOGGER.exception("Unexpected error polling router")
            raise UpdateFailed(f"Unexpected error: {err}") from err
        data.fetched_at = dt_util.utcnow()
        return data

    async def async_set_guest_5g(
        self, func: Callable[..., GuestWifi], *args: Any
    ) -> None:
        """Run a guest Wi-Fi change in the executor and publish the new state.

        Updates entities immediately from the router's read-back, without
        waiting for the next scheduled poll.
        """
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                guest = await self.hass.async_add_executor_job(func, *args)
        except TimeoutError as err:
            raise HomeAssistantError("Timed out talking to router") from err
        except RouterAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(f"Router rejected the password: {err}") from err
        except RouterError as err:
            raise HomeAssistantError(str(err)) from err

        if self.data is not None:
            self.async_set_updated_data(replace(self.data, guest_5g=guest))
