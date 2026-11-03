"""Guest Wi-Fi switch for TP-Link Router Care."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import RouterConfigEntry, RouterCoordinator
from .entity import RouterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up switches (only if the router exposes the 5 GHz guest network)."""
    coordinator = entry.runtime_data
    if coordinator.data and coordinator.data.guest_5g is not None:
        async_add_entities([RouterGuest5gSwitch(coordinator)])


class RouterGuest5gSwitch(RouterEntity, SwitchEntity):
    """Turns the 5 GHz guest Wi-Fi on and off."""

    _attr_translation_key = "guest_5g"

    def __init__(self, coordinator: RouterCoordinator) -> None:
        super().__init__(coordinator, "guest_5g")

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.guest_5g is not None
        )

    @property
    def is_on(self) -> bool | None:
        guest = self.coordinator.data.guest_5g if self.coordinator.data else None
        return guest.enabled if guest else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_guest_5g(
            self.coordinator.api.set_guest_5g_enabled, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_set_guest_5g(
            self.coordinator.api.set_guest_5g_enabled, False
        )
