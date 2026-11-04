"""TP-Link Router Care integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant

from .api import RouterApi
from .const import CONF_CLIENT_CLASS, DEFAULT_USERNAME
from .coordinator import RouterConfigEntry, RouterCoordinator
from .qr_webhook import async_setup_qr_webhook

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.IMAGE,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: RouterConfigEntry) -> bool:
    """Set up the router from a config entry."""
    api = RouterApi(
        host=entry.data[CONF_HOST],
        username=entry.data.get(CONF_USERNAME, DEFAULT_USERNAME),
        password=entry.data[CONF_PASSWORD],
        client_class=entry.data.get(CONF_CLIENT_CLASS),
    )
    coordinator = RouterCoordinator(hass, entry, api)

    # Raises ConfigEntryNotReady / ConfigEntryAuthFailed on failure, which makes
    # HA retry in the background instead of failing startup.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    async_setup_qr_webhook(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RouterConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
