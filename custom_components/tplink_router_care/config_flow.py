"""Config flow for TP-Link Router Care."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    RouterApi,
    RouterAuthError,
    RouterConnectionError,
    RouterUnsupportedError,
    normalize_host,
)
from .const import (
    CONF_CLIENT_CLASS,
    CONF_QR_THEME,
    DEFAULT_QR_THEME,
    DEFAULT_USERNAME,
    DETECT_TIMEOUT,
    DOMAIN,
    QR_THEMES,
    REACHABILITY_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


async def _async_is_reachable(host: str) -> bool:
    """Fast, non-blocking TCP check so a wrong IP fails in seconds, not minutes."""
    parts = urlsplit(host if "://" in host else f"http://{host}")
    hostname = parts.hostname
    if not hostname:
        return False
    try:
        port = parts.port or (443 if parts.scheme == "https" else 80)
    except ValueError:
        return False
    try:
        async with asyncio.timeout(REACHABILITY_TIMEOUT):
            _, writer = await asyncio.open_connection(hostname, port)
    except (OSError, TimeoutError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except OSError:
        pass
    return True


class TplinkRouterCareConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options (the integration's Configure button)."""
        return TplinkRouterCareOptionsFlow()

    async def _async_validate(
        self, host: str, username: str, password: str
    ) -> tuple[dict[str, str], RouterApi | None, str | None]:
        """Return (errors, api, title). All blocking work runs in the executor."""
        if not await _async_is_reachable(host):
            return {"base": "cannot_connect"}, None, None

        api = RouterApi(host, username, password)
        try:
            async with asyncio.timeout(DETECT_TIMEOUT):
                firmware = await self.hass.async_add_executor_job(api.detect)
        except RouterConnectionError:
            return {"base": "cannot_connect"}, None, None
        except RouterAuthError:
            return {"base": "invalid_auth"}, None, None
        except RouterUnsupportedError:
            return {"base": "login_failed"}, None, None
        except TimeoutError:
            return {"base": "timeout"}, None, None
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error while setting up router")
            return {"base": "unknown"}, None, None

        title = f"TP-Link {firmware.model}" if firmware and firmware.model else host
        return {}, api, title

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for router IP and password."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            username = user_input.get(CONF_USERNAME) or DEFAULT_USERNAME
            password = user_input[CONF_PASSWORD]

            self._async_abort_entries_match({CONF_HOST: host})

            errors, api, title = await self._async_validate(host, username, password)
            if not errors and api is not None:
                return self.async_create_entry(
                    title=title or host,
                    data={
                        CONF_HOST: host,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_CLIENT_CLASS: api.client_class,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Router rejected the stored password."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()

        if user_input is not None:
            host = entry.data[CONF_HOST]
            username = entry.data.get(CONF_USERNAME, DEFAULT_USERNAME)
            errors, api, _ = await self._async_validate(
                host, username, user_input[CONF_PASSWORD]
            )
            if not errors and api is not None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_CLIENT_CLASS: api.client_class,
                    },
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )


class TplinkRouterCareOptionsFlow(OptionsFlow):
    """Display options. Changing them never contacts the router."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_QR_THEME,
                    default=self.config_entry.options.get(CONF_QR_THEME, DEFAULT_QR_THEME),
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=QR_THEMES,
                        translation_key="qr_theme",
                        mode=SelectSelectorMode.LIST,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
