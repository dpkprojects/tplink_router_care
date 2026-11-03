"""Base entity for TP-Link Router Care."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import RouterCoordinator


class RouterEntity(CoordinatorEntity[RouterCoordinator]):
    """Common device info / naming."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: RouterCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        host: str = entry.data[CONF_HOST]
        self._attr_unique_id = f"{entry.entry_id}_{key}"

        firmware = coordinator.data.firmware if coordinator.data else None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            manufacturer="TP-Link",
            name=entry.title,
            model=firmware.model if firmware else None,
            sw_version=firmware.firmware_version if firmware else None,
            hw_version=firmware.hardware_version if firmware else None,
            configuration_url=host if "://" in host else f"http://{host}",
        )
