"""Guest Wi-Fi QR code image for TP-Link Router Care."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import GuestWifi
from .const import CONF_QR_THEME, DEFAULT_QR_THEME
from .coordinator import RouterConfigEntry, RouterCoordinator
from .entity import RouterEntity
from .qr import render_wifi_card, wifi_qr_payload


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RouterConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the QR image (only if the router exposes the 5 GHz guest network)."""
    coordinator = entry.runtime_data
    if coordinator.data and coordinator.data.guest_5g is not None:
        async_add_entities([RouterGuest5gQrImage(coordinator)])


class RouterGuest5gQrImage(RouterEntity, ImageEntity):
    """Wi-Fi card (QR code + network name + password), generated in memory.

    Built from the SSID and password read from the router, so it changes
    whenever the router reports a new password (button press, refresh or the
    scheduled poll). The light/dark theme comes from the integration's options and
    applies immediately when changed, without contacting the router.
    """

    _attr_translation_key = "guest_5g_qr"
    _attr_content_type = "image/png"

    def __init__(self, coordinator: RouterCoordinator) -> None:
        super().__init__(coordinator, "guest_5g_qr")
        ImageEntity.__init__(self, coordinator.hass)
        self._guest = self._current_guest()
        self._payload = wifi_qr_payload(self._guest) if self._guest else None
        self._theme = self._current_theme()
        self._png: bytes | None = None
        self._attr_image_last_updated = dt_util.utcnow()

    def _current_theme(self) -> str:
        return self.coordinator.config_entry.options.get(CONF_QR_THEME, DEFAULT_QR_THEME)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.config_entry.add_update_listener(self._async_options_updated)
        )

    async def _async_options_updated(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Re-render with the new theme (no reload, no router login)."""
        theme = self._current_theme()
        if theme != self._theme:
            self._theme = theme
            self._invalidate()
            self.async_write_ha_state()

    @callback
    def _invalidate(self) -> None:
        self._png = None
        # Tells the frontend to fetch the new image.
        self._attr_image_last_updated = dt_util.utcnow()

    def _current_guest(self) -> GuestWifi | None:
        data = self.coordinator.data
        return data.guest_5g if data else None

    @property
    def available(self) -> bool:
        return super().available and self._payload is not None

    @callback
    def _handle_coordinator_update(self) -> None:
        guest = self._current_guest()
        payload = wifi_qr_payload(guest) if guest else None
        if payload != self._payload:
            self._guest = guest
            self._payload = payload
            self._invalidate()
        super()._handle_coordinator_update()

    async def async_image(self) -> bytes | None:
        payload, guest, theme = self._payload, self._guest, self._theme
        if payload is None or guest is None:
            return None
        if self._png is None:
            png = await self.hass.async_add_executor_job(render_wifi_card, guest, theme)
            if (payload, theme) == (self._payload, self._theme):  # still current
                self._png = png
            return png
        return self._png
