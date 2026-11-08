"""Sensors for TP-Link Router Care."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import RouterData
from .coordinator import RouterConfigEntry, RouterCoordinator
from .entity import RouterEntity


def _pct(value: float | None) -> float | None:
    """Library reports usage as a 0..1 fraction."""
    return round(value * 100, 1) if value is not None else None


@dataclass(frozen=True, kw_only=True)
class RouterSensorDescription(SensorEntityDescription):
    """Sensor description with a value extractor."""

    value_fn: Callable[[RouterData], Any]
    # Only create the entity if the router actually reports this value.
    optional: bool = False
    # Keep showing the last value when a poll fails instead of going unavailable.
    keep_on_failure: bool = False


SENSORS: tuple[RouterSensorDescription, ...] = (
    RouterSensorDescription(
        key="last_sync",
        translation_key="last_sync",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: d.fetched_at,
        # The point is to show how stale the data is while the router is down.
        keep_on_failure=True,
    ),
    RouterSensorDescription(
        key="clients_total",
        translation_key="clients_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.status.clients_total,
    ),
    RouterSensorDescription(
        key="wifi_clients_total",
        translation_key="wifi_clients_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.status.wifi_clients_total,
    ),
    RouterSensorDescription(
        key="wired_clients_total",
        translation_key="wired_clients_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.status.wired_total,
    ),
    RouterSensorDescription(
        key="guest_clients_total",
        translation_key="guest_clients_total",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.status.guest_clients_total,
    ),
    RouterSensorDescription(
        key="guest_5g_password",
        translation_key="guest_5g_password",
        value_fn=lambda d: d.guest_5g.password,
        optional=True,
    ),
    RouterSensorDescription(
        key="guest_5g_ssid",
        translation_key="guest_5g_ssid",
        value_fn=lambda d: d.guest_5g.ssid,
        optional=True,
    ),
    RouterSensorDescription(
        key="cpu_usage",
        translation_key="cpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _pct(d.status.cpu_usage),
        optional=True,
    ),
    RouterSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _pct(d.status.mem_usage),
        optional=True,
    ),
    RouterSensorDescription(
        key="wan_ip",
        translation_key="wan_ip",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.status.wan_ipv4_addr,
        optional=True,
    ),
    RouterSensorDescription(
        key="wan_uptime",
        translation_key="wan_uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        suggested_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.status.wan_ipv4_uptime,
        optional=True,
    ),
)


def _safe_value(desc: RouterSensorDescription, data: RouterData | None) -> Any:
    if data is None:
        return None
    try:
        return desc.value_fn(data)
    except (AttributeError, TypeError, ValueError):
        return None


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        RouterSensor(coordinator, desc)
        for desc in SENSORS
        if not desc.optional or _safe_value(desc, coordinator.data) is not None
    )


class RouterSensor(RouterEntity, SensorEntity):
    """A router status value."""

    entity_description: RouterSensorDescription

    def __init__(
        self, coordinator: RouterCoordinator, description: RouterSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if self.entity_description.keep_on_failure:
            return self.coordinator.data is not None
        return super().available

    @property
    def native_value(self) -> Any:
        return _safe_value(self.entity_description, self.coordinator.data)
