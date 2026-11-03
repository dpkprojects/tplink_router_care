"""Data update coordinator for TP-Link Router Care."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import RouterApi, RouterAuthError, RouterData, RouterError
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
