"""Webhook that serves the guest Wi-Fi QR card PNG.

GET /api/webhook/<webhook_id>[?theme=light|dark]

Lets things outside Home Assistant (a wall tablet, e-ink display, kiosk page,
script) fetch the card without an HA access token. Turned on/off and given a
fixed ID in the integration's options; changes apply immediately, without a
reload or router login. The card contains the guest Wi-Fi password, so the
webhook only answers requests from the local network and the ID acts as the
secret.
"""

from __future__ import annotations

from http import HTTPStatus
import logging
import re

from aiohttp import web

from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_WEBHOOK_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import NoURLAvailableError

from .const import (
    CONF_QR_THEME,
    CONF_WEBHOOK_ENABLED,
    DEFAULT_QR_THEME,
    DEFAULT_WEBHOOK_ENABLED,
    DOMAIN,
    QR_THEMES,
)
from .coordinator import RouterConfigEntry
from .qr import render_wifi_card

_LOGGER = logging.getLogger(__name__)

# Characters that are safe in a URL path segment without escaping.
WEBHOOK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# The password changes; clients must never show a stale card.
_NO_CACHE = {"Cache-Control": "no-store"}


@callback
def async_ensure_webhook_id(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the entry's webhook ID, generating and saving one on first use.

    Stored in options so it can be edited. Older builds kept it in data; that
    value is moved over so an existing URL keeps working.
    """
    webhook_id: str | None = entry.options.get(CONF_WEBHOOK_ID)
    if webhook_id:
        return webhook_id
    data = dict(entry.data)
    webhook_id = data.pop(CONF_WEBHOOK_ID, None) or webhook.async_generate_id()
    hass.config_entries.async_update_entry(
        entry, data=data, options={**entry.options, CONF_WEBHOOK_ID: webhook_id}
    )
    return webhook_id


def webhook_enabled(entry: ConfigEntry) -> bool:
    return bool(entry.options.get(CONF_WEBHOOK_ENABLED, DEFAULT_WEBHOOK_ENABLED))


@callback
def async_webhook_id_in_use(hass: HomeAssistant, webhook_id: str) -> bool:
    """True if any integration currently has a webhook with this ID."""
    return webhook_id in hass.data.get(webhook.DOMAIN, {})


@callback
def async_webhook_url(hass: HomeAssistant, webhook_id: str) -> str:
    """Full local URL of the webhook, or just its path if HA has no URL configured."""
    try:
        return webhook.async_generate_url(
            hass, webhook_id, allow_ip=True, prefer_external=False
        )
    except NoURLAvailableError:
        return webhook.async_generate_path(webhook_id)


class QrWebhook:
    """Keeps the registered webhook in line with the entry's options."""

    def __init__(self, hass: HomeAssistant, entry: RouterConfigEntry) -> None:
        self._hass = hass
        self._entry = entry
        self._registered: str | None = None

    @callback
    def async_sync(self) -> None:
        """Register, unregister or move the webhook to match the options."""
        wanted = (
            self._entry.options.get(CONF_WEBHOOK_ID)
            if webhook_enabled(self._entry)
            else None
        )
        if wanted == self._registered:
            return
        self.async_unregister()
        if not wanted:
            return
        try:
            webhook.async_register(
                self._hass,
                DOMAIN,
                f"{self._entry.title} guest Wi-Fi QR code",
                wanted,
                self._async_handle,
                local_only=True,
                allowed_methods=["GET", "HEAD"],
            )
        except ValueError:
            _LOGGER.error(
                "Guest Wi-Fi QR webhook not started: another integration already "
                "uses that webhook ID. Choose a different one under Configure"
            )
            return
        self._registered = wanted

    @callback
    def async_unregister(self) -> None:
        if self._registered:
            webhook.async_unregister(self._hass, self._registered)
            self._registered = None

    async def _async_handle(
        self, hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        entry = self._entry
        theme = request.query.get("theme") or entry.options.get(
            CONF_QR_THEME, DEFAULT_QR_THEME
        )
        if theme not in QR_THEMES:
            return web.Response(
                status=HTTPStatus.BAD_REQUEST,
                text=f"theme must be one of: {', '.join(QR_THEMES)}",
            )

        data = entry.runtime_data.data
        guest = data.guest_5g if data else None
        if guest is None:
            return web.Response(
                status=HTTPStatus.NOT_FOUND,
                text="This router does not expose a 5 GHz guest network.",
            )

        png = await hass.async_add_executor_job(render_wifi_card, guest, theme)
        if png is None:
            return web.Response(
                status=HTTPStatus.SERVICE_UNAVAILABLE,
                text="Guest network name or password not known yet.",
                headers=_NO_CACHE,
            )
        return web.Response(body=png, content_type="image/png", headers=_NO_CACHE)


@callback
def async_setup_qr_webhook(hass: HomeAssistant, entry: RouterConfigEntry) -> None:
    """Start the webhook (if enabled) and follow option changes until unload."""
    async_ensure_webhook_id(hass, entry)
    hook = QrWebhook(hass, entry)
    hook.async_sync()

    async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
        hook.async_sync()

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    entry.async_on_unload(hook.async_unregister)
