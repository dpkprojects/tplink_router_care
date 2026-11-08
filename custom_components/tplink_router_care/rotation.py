"""Automatic guest Wi-Fi password rotation (which also renews the QR code).

The time of the last password change is kept in the config entry's data, so
the schedule survives Home Assistant restarts: a monthly rotation still happens
once a month even if HA restarts every day, and a rotation that fell due while
HA was off runs shortly after startup.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, callback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .api import generate_password
from .const import (
    CONF_GUEST_ROTATION,
    DATA_GUEST_ROTATED_AT,
    DEFAULT_GUEST_ROTATION,
    ROTATION_MIN_DELAY,
    ROTATION_RETRY,
)

if TYPE_CHECKING:
    from .coordinator import RouterCoordinator

_LOGGER = logging.getLogger(__name__)


def rotation_interval(entry: ConfigEntry) -> timedelta | None:
    """Rotation interval from the options; None means rotation is off."""
    try:
        hours = int(entry.options.get(CONF_GUEST_ROTATION, DEFAULT_GUEST_ROTATION))
    except (TypeError, ValueError):
        hours = 0
    return timedelta(hours=hours) if hours > 0 else None


class GuestPasswordRotator:
    """Changes the guest Wi-Fi password on a schedule and on demand."""

    def __init__(self, coordinator: RouterCoordinator) -> None:
        self._coordinator = coordinator
        self._entry = coordinator.config_entry
        # Seeded from the options so the first schedule at startup isn't
        # mistaken for "rotation was just switched on".
        self._interval = rotation_interval(self._entry)
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def _supported(self) -> bool:
        data = self._coordinator.data
        return data is not None and data.guest_5g is not None

    def _last_rotation(self) -> datetime | None:
        value = self._entry.data.get(DATA_GUEST_ROTATED_AT)
        return dt_util.parse_datetime(value) if isinstance(value, str) else None

    @callback
    def _save_rotation(self, when: datetime) -> None:
        self._coordinator.hass.config_entries.async_update_entry(
            self._entry,
            data={**self._entry.data, DATA_GUEST_ROTATED_AT: when.isoformat()},
        )

    @callback
    def async_cancel(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def async_schedule(self) -> None:
        """(Re)arm the timer to match the options. Safe to call repeatedly."""
        interval = rotation_interval(self._entry)
        just_enabled = self._interval is None and interval is not None
        self._interval = interval
        self.async_cancel()
        if interval is None or not self._supported:
            return

        now = dt_util.utcnow()
        # Switching rotation on starts the clock now, so the first change comes
        # one full interval later rather than straight away.
        last = None if just_enabled else self._last_rotation()
        if last is None:
            last = now
            self._save_rotation(now)
        # Never fire right away: a rotation missed while HA was off (or after
        # shortening the interval) waits a moment so startup isn't crowded.
        due = max(last + interval, now + ROTATION_MIN_DELAY)
        self._arm(due)
        _LOGGER.debug("Next guest Wi-Fi password rotation at %s", due)

    @callback
    def _arm(self, when: datetime) -> None:
        self._unsub = async_track_point_in_utc_time(
            self._coordinator.hass, self._async_timer_fired, when
        )

    async def _async_timer_fired(self, _now: datetime) -> None:
        self._unsub = None
        try:
            await self.async_rotate_now()
        except Exception as err:  # noqa: BLE001 - keep the schedule alive
            _LOGGER.warning(
                "Automatic guest Wi-Fi password change failed, retrying in %s: %s",
                ROTATION_RETRY,
                err,
            )
            if self._unsub is None:
                self._arm(dt_util.utcnow() + ROTATION_RETRY)

    async def async_rotate_now(self) -> None:
        """Set a new random guest password and restart the rotation clock.

        Used by the timer and by the "New guest 5 GHz password" button, so a
        manual change also postpones the next automatic one.
        """
        coordinator = self._coordinator
        await coordinator.async_set_guest_5g(
            coordinator.api.set_guest_5g_password, generate_password()
        )
        # Saving updates the entry, which re-runs async_schedule through the
        # update listener; schedule here too so the timer never depends on it.
        self._save_rotation(dt_util.utcnow())
        self.async_schedule()
