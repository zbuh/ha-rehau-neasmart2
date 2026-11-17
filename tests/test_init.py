"""Integration tests inside Home Assistant, with the real TCP server and a fake converter."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.rehau_neasmart2 import dpt9
from custom_components.rehau_neasmart2.const import (
    CONF_LISTEN_PORT,
    CONF_MIXED_GROUPS,
    CONF_NAME,
    CONF_PUMPS,
    CONF_ZONES,
    DOMAIN,
    MIGRATION_LISTEN_PORT,
)

from .helpers import FakeConverter, load_capture, read_holding, write_multiple

ZONES = "1.1:Basement,1.5:Living room,1.6:Kitchen,1.9:Suite,1.10:Room A,1.11:Room B"


def _entry(port: int, **extra) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=2,
        unique_id="rehau",
        title="rehau Climate Control System",
        data={
            CONF_NAME: "rehau",
            CONF_LISTEN_PORT: port,
            CONF_ZONES: ZONES,
            CONF_MIXED_GROUPS: 1,
            CONF_PUMPS: "1",
            **extra,
        },
    )


async def _settle(hass: HomeAssistant) -> None:
    await asyncio.sleep(0.6)  # entity notification debounce
    await hass.async_block_till_done()


async def _setup_with_capture(hass: HomeAssistant, port: int) -> tuple[MockConfigEntry, FakeConverter]:
    entry = _entry(port)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    conv = FakeConverter()
    await conv.connect(port)
    await conv.replay(load_capture())
    await _settle(hass)
    return entry, conv


def _entity_id(hass: HomeAssistant, platform: str, unique_id: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, unique_id)
    assert entity_id, unique_id
    return entity_id


async def test_entities_from_real_capture(hass: HomeAssistant, free_port: int) -> None:
    entry, conv = await _setup_with_capture(hass, free_port)
    try:
        climate = hass.states.get(_entity_id(hass, "climate", "rehau_1_1_thermostat"))
        assert climate.state == "auto"
        assert climate.attributes["preset_mode"] == "Normal"
        assert climate.attributes["temperature"] == 23.0
        assert 20 < climate.attributes["current_temperature"] < 26
        assert climate.attributes["current_humidity"] > 0

        outside = hass.states.get(_entity_id(hass, "sensor", "rehau_outside_temperature"))
        assert 15 < float(outside.state) < 20
        assert hass.states.get(_entity_id(hass, "sensor", "rehau_errors_presence")).state == "Not Present"
        assert hass.states.get(_entity_id(hass, "sensor", "rehau_1_mixedgroup_pump_state")).state == "On"
        assert hass.states.get(_entity_id(hass, "sensor", "rehau_1_extra_pump_state")).state == "On"
        assert hass.states.get(_entity_id(hass, "binary_sensor", "rehau_sysbus_connection")).state == "on"
        # global mode is never broadcast by the base: unknown until written
        mode = hass.states.get(_entity_id(hass, "select", "rehau_global_climate_mode"))
        assert mode.state == "unknown"
    finally:
        await conv.close()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def _base_reads(conv: FakeConverter, addr: int, count: int, times: int = 20) -> bytes:
    """Emulate the base polling a command register."""
    response = b""
    for _ in range(times):
        await asyncio.sleep(0.05)
        response = await conv.request(240, read_holding(addr, count))
    return response


async def test_commands_are_confirmed_by_base_read(hass: HomeAssistant, free_port: int) -> None:
    entry, conv = await _setup_with_capture(hass, free_port)
    climate_id = _entity_id(hass, "climate", "rehau_1_1_thermostat")
    mode_id = _entity_id(hass, "select", "rehau_global_climate_mode")
    try:
        poll = asyncio.create_task(_base_reads(conv, 100, 2))
        await hass.services.async_call(
            "climate", "set_temperature", {"entity_id": climate_id, "temperature": 22.5}, blocking=True
        )
        response = await poll
        assert response[2:] == bytes.fromhex("0001") + dpt9.pack(22.5).to_bytes(2, "big")
        await _settle(hass)
        assert hass.states.get(climate_id).attributes["temperature"] == 22.5

        poll = asyncio.create_task(_base_reads(conv, 100, 1))
        await hass.services.async_call(
            "climate", "set_preset_mode", {"entity_id": climate_id, "preset_mode": "Standby"}, blocking=True
        )
        await poll
        # the base answers with the standby sentinel for the setpoint
        await conv.send(0, write_multiple(101, [0x8116]))
        await _settle(hass)
        state = hass.states.get(climate_id)
        assert state.attributes["preset_mode"] == "Standby"
        assert state.attributes["temperature"] is None

        poll = asyncio.create_task(_base_reads(conv, 1, 2))
        await hass.services.async_call(
            "select", "select_option", {"entity_id": mode_id, "option": "Forced Heating"}, blocking=True
        )
        assert (await poll)[2:4] == bytes.fromhex("0004")
        await _settle(hass)
        assert hass.states.get(mode_id).state == "Forced Heating"
    finally:
        await conv.close()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_command_fails_without_converter(hass: HomeAssistant, free_port: int) -> None:
    entry = _entry(free_port)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    mode_id = _entity_id(hass, "select", "rehau_global_climate_mode")
    assert hass.states.get(mode_id).state == STATE_UNAVAILABLE
    with pytest.raises(HomeAssistantError):
        await entry.runtime_data.async_command(1, 4, "test")
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_command_times_out_when_base_does_not_read(hass: HomeAssistant, free_port: int) -> None:
    entry, conv = await _setup_with_capture(hass, free_port)
    try:
        with (
            patch("custom_components.rehau_neasmart2.hub.COMMAND_CONFIRM_TIMEOUT", 0.3),
            pytest.raises(HomeAssistantError, match="did not read"),
        ):
            await entry.runtime_data.async_command(1, 4, "mode")
        assert entry.runtime_data.bank.pending() == {}
    finally:
        await conv.close()
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_values_survive_restart(hass: HomeAssistant, free_port: int) -> None:
    entry, conv = await _setup_with_capture(hass, free_port)
    # zone 1.1 changed on the thermostat during the capture, so the base broadcast it
    climate_id = _entity_id(hass, "climate", "rehau_1_1_thermostat")
    await conv.close()
    assert await hass.config_entries.async_unload(entry.entry_id)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    try:
        state = hass.states.get(climate_id)  # restored from storage, still fresh
        assert state.attributes["temperature"] == 23.0
        assert state.attributes["preset_mode"] == "Normal"
        # but the zone command registers are not served to the base
        assert entry.runtime_data.bank.bus_read(100, 2) == [0, 0]
    finally:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_migration_from_rest_addon(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        version=1,
        minor_version=1,
        title="rehau Climate Control System",
        data={
            CONF_NAME: "rehau",
            "neasmart_gw_server_host": "192.0.2.10",
            "neasmart_gw_server_port": 5000,
            CONF_ZONES: "1.1:Basement",
            CONF_MIXED_GROUPS: 1,
        },
    )
    entry.add_to_hass(hass)
    with patch("custom_components.rehau_neasmart2.hub.SysbusServer.start"):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 1 and entry.minor_version == 2
    assert entry.data[CONF_LISTEN_PORT] == MIGRATION_LISTEN_PORT
    assert entry.data["neasmart_gw_server_host"] == "192.0.2.10"  # kept for rollback
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_port_in_use_retries(hass: HomeAssistant, free_port: int) -> None:
    blocker = await asyncio.start_server(lambda r, w: None, "0.0.0.0", free_port)
    try:
        entry = _entry(free_port)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY
    finally:
        blocker.close()
        await blocker.wait_closed()
