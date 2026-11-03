"""Data update coordinator for TP-Link Router Care."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import GuestWifi, RouterApi, RouterAuthError, RouterData, RouterError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, UPDATE_TIMEOUT

_LOGGER = logging.getLogger(__name__)

type RouterConfigEntry = ConfigEntry[RouterCoordinator]


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
            update_interval=DEFAULT_SCAN_INTERVAL,
        )
        self.api = api

    async def _async_update_data(self) -> RouterData:
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                return await self.hass.async_add_executor_job(self.api.fetch)
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

    async def async_set_guest_5g(
        self, func: Callable[..., GuestWifi], *args: Any
    ) -> None:
        """Run a guest Wi-Fi change in the executor and publish the new state.

        Updates entities immediately from the router's read-back, without
        waiting for the next hourly poll.
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
