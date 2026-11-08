"""Buttons for TP-Link Router Care."""

from __future__ import annotations

import asyncio

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import RouterError, generate_password
from .const import UPDATE_TIMEOUT
from .coordinator import RouterConfigEntry, RouterCoordinator
from .entity import RouterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up buttons."""
    coordinator = entry.runtime_data
    entities: list[ButtonEntity] = [
        RouterRefreshButton(coordinator),
        RouterRebootButton(coordinator),
    ]
    if coordinator.data and coordinator.data.guest_5g is not None:
        entities.append(RouterGuest5gPasswordButton(coordinator))
    async_add_entities(entities)


class RouterRefreshButton(RouterEntity, ButtonEntity):
    """Polls the router immediately instead of waiting for the next scheduled poll."""

    _attr_translation_key = "refresh"

    def __init__(self, coordinator: RouterCoordinator) -> None:
        super().__init__(coordinator, "refresh")

    @property
    def available(self) -> bool:
        # Must stay pressable after a failed poll, otherwise you could only
        # retry by waiting for the next scheduled poll.
        return True

    async def async_press(self) -> None:
        # Runs one poll now (in the executor, via the coordinator) and restarts
        # the polling timer. All entities update from the result.
        await self.coordinator.async_refresh()
        if not self.coordinator.last_update_success:
            raise HomeAssistantError(
                f"Refresh failed: {self.coordinator.last_exception}"
            )


class RouterGuest5gPasswordButton(RouterEntity, ButtonEntity):
    """Sets a new random 8-character password on the 5 GHz guest Wi-Fi."""

    _attr_translation_key = "guest_5g_new_password"

    def __init__(self, coordinator: RouterCoordinator) -> None:
        super().__init__(coordinator, "guest_5g_new_password")

    async def async_press(self) -> None:
        # The "Guest 5 GHz password" sensor updates from the router's read-back.
        await self.coordinator.async_set_guest_5g(
            self.coordinator.api.set_guest_5g_password, generate_password()
        )


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
