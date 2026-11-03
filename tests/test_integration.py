"""Tests for TP-Link Router Care."""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tplinkrouterc6u import Firmware, Status

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.tplink_router_care.api import (
    RouterApi,
    RouterAuthError,
    RouterConnectionError,
    RouterData,
    RouterUnsupportedError,
)
from custom_components.tplink_router_care.const import CONF_CLIENT_CLASS, DOMAIN

FIRMWARE = Firmware("Archer AX55 v1.0", "Archer AX55", "1.2.3 Build 2025")
REACHABLE = "custom_components.tplink_router_care.config_flow._async_is_reachable"
ENTRY_DATA = {
    CONF_HOST: "192.168.0.1",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
    CONF_CLIENT_CLASS: "TplinkRouter",
}


def _status() -> Status:
    s = Status()
    s.clients_total = 7
    s.wifi_clients_total = 5
    s.wired_total = 2
    s.cpu_usage = 0.25
    s.mem_usage = 0.5
    s.wan_ipv4_uptime = 3600
    return s


def _assert_off_loop() -> None:
    """Fail loudly if a blocking library call runs on the event loop thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise AssertionError("Blocking router call executed inside the event loop!")


def fake_fetch(self: RouterApi) -> RouterData:
    _assert_off_loop()
    return RouterData(firmware=FIRMWARE, status=_status())


def fake_detect(self: RouterApi) -> Firmware:
    _assert_off_loop()
    self._client_class = "TplinkRouter"
    return FIRMWARE


# --------------------------------------------------------------------- config flow


async def test_flow_success(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    with patch(REACHABLE, return_value=True), patch.object(RouterApi, "detect", fake_detect), \
         patch.object(RouterApi, "fetch", fake_fetch):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: " 192.168.0.1/ ", CONF_PASSWORD: "secret"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "TP-Link Archer AX55"
    assert result["data"] == ENTRY_DATA


@pytest.mark.parametrize(
    ("reachable", "exc", "error"),
    [
        (False, None, "cannot_connect"),
        (True, RouterConnectionError("x"), "cannot_connect"),
        (True, RouterAuthError("x"), "invalid_auth"),
        (True, RouterUnsupportedError("x"), "login_failed"),
        (True, KeyError("library bug"), "unknown"),
    ],
)
async def test_flow_errors(hass: HomeAssistant, reachable, exc, error) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(REACHABLE, return_value=reachable), patch.object(RouterApi, "detect", side_effect=exc):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.0.1", CONF_PASSWORD: "bad"}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_flow_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.0.1", CONF_PASSWORD: "x"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_flow_does_not_block_loop(hass: HomeAssistant) -> None:
    """A slow router must not freeze HA: other loop tasks keep running."""
    release = threading.Event()

    def slow_detect(self):
        _assert_off_loop()
        release.wait(5)
        self._client_class = "TplinkRouter"
        return FIRMWARE

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(REACHABLE, return_value=True), patch.object(RouterApi, "detect", slow_detect), \
         patch.object(RouterApi, "fetch", fake_fetch):
        task = hass.async_create_task(
            hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_HOST: "192.168.0.1", CONF_PASSWORD: "secret"}
            )
        )
        ticks = 0
        for _ in range(10):
            await asyncio.sleep(0.01)
            ticks += 1
        assert ticks == 10 and not task.done()  # loop alive while router is "slow"
        release.set()
        result = await task
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


# --------------------------------------------------------------------- setup


async def test_setup_creates_entities(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title="TP-Link Archer AX55")
    entry.add_to_hass(hass)
    with patch.object(RouterApi, "fetch", fake_fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.tp_link_archer_ax55_connected_clients").state == "7"
    assert hass.states.get("sensor.tp_link_archer_ax55_cpu_usage").state == "25.0"
    assert hass.states.get("sensor.tp_link_archer_ax55_memory_usage").state == "50.0"
    assert hass.states.get("binary_sensor.tp_link_archer_ax55_router_reachable").state == "on"
    assert hass.states.get("button.tp_link_archer_ax55_restart") is not None
    # WAN IP not reported -> optional sensor not created.
    assert hass.states.get("sensor.tp_link_archer_ax55_wan_ip_address") is None

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.parametrize("exc", [RouterConnectionError("down"), RuntimeError("library bug")])
async def test_setup_router_down_retries(hass: HomeAssistant, exc) -> None:
    """Router offline / library crash -> entry retries, HA keeps running."""
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    with patch.object(RouterApi, "fetch", side_effect=exc):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_bad_password_starts_reauth(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA)
    entry.add_to_hass(hass)
    with patch.object(RouterApi, "fetch", side_effect=RouterAuthError("nope")):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]

    with patch(REACHABLE, return_value=True), patch.object(RouterApi, "detect", fake_detect), \
         patch.object(RouterApi, "fetch", fake_fetch):
        result = await hass.config_entries.flow.async_configure(
            flows[0]["flow_id"], {CONF_PASSWORD: "new"}
        )
        await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"
    assert entry.state is ConfigEntryState.LOADED


async def test_poll_failure_marks_unavailable(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title="R")
    entry.add_to_hass(hass)
    with patch.object(RouterApi, "fetch", fake_fetch):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    with patch.object(RouterApi, "fetch", side_effect=RouterConnectionError("gone")):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get("sensor.r_connected_clients").state == "unavailable"
    assert hass.states.get("binary_sensor.r_router_reachable").state == "off"


async def test_reboot_button(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, title="R")
    entry.add_to_hass(hass)
    called = []

    def fake_reboot(self):
        _assert_off_loop()
        called.append(True)

    with patch.object(RouterApi, "fetch", fake_fetch), patch.object(RouterApi, "reboot", fake_reboot):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call("button", "press", {"entity_id": "button.r_restart"}, blocking=True)
    assert called == [True]


# --------------------------------------------------------------------- real library


async def test_real_library_runs_in_executor(hass: HomeAssistant, socket_enabled) -> None:
    """Drive the real tplinkrouterc6u code against a closed port.

    HA's blocking-I/O guard would raise if any socket call happened on the loop;
    instead we expect a clean SETUP_RETRY.
    """
    entry = MockConfigEntry(domain=DOMAIN, data={**ENTRY_DATA, CONF_HOST: "127.0.0.1:1"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
