"""Reboot button for TP-Link Router Care."""

from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import RouterError
from .const import UPDATE_TIMEOUT
from .coordinator import RouterConfigEntry, RouterCoordinator
from .entity import RouterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    async_add_entities([RouterRebootButton(entry.runtime_data)])


class RouterRebootButton(RouterEntity, ButtonEntity):
    """Reboots the router."""

    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: RouterCoordinator) -> None:
        super().__init__(coordinator, "reboot")

    async def async_press(self) -> None:
        try:
            async with asyncio.timeout(UPDATE_TIMEOUT):
                await self.hass.async_add_executor_job(self.coordinator.api.reboot)
        except TimeoutError as err:
            raise HomeAssistantError("Timed out sending reboot command") from err
        except RouterError as err:
            raise HomeAssistantError(f"Reboot failed: {err}") from err
