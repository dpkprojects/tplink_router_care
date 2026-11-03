"""Connectivity binary sensor for TP-Link Router Care."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import RouterConfigEntry, RouterCoordinator
from .entity import RouterEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors."""
    async_add_entities([RouterConnectivitySensor(entry.runtime_data)])


class RouterConnectivitySensor(RouterEntity, BinarySensorEntity):
    """On when the last poll to the router succeeded."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "router_reachable"

    def __init__(self, coordinator: RouterCoordinator) -> None:
        super().__init__(coordinator, "router_reachable")

    @property
    def available(self) -> bool:
        # Always available: its whole job is to report "off" when unreachable.
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success
