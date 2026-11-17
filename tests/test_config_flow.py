"""Config and options flow."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.rehau_neasmart2.const import (
    CONF_DEHUMIDIFIERS,
    CONF_LISTEN_PORT,
    CONF_MIXED_GROUPS,
    CONF_NAME,
    CONF_PUMPS,
    CONF_RESTORE_GLOBALS,
    CONF_STALE_AFTER,
    CONF_ZONES,
    DOMAIN,
)
from custom_components.rehau_neasmart2.hub import async_discover
from custom_components.rehau_neasmart2.topology import Detected

from .helpers import FakeConverter, load_capture

DETECTED = Detected(zones=[(1, 1), (1, 5)], mixed_groups=[1], pumps=[1])


async def _run_progress(hass: HomeAssistant, result: dict) -> dict:
    """Finish the discovery step (it may complete eagerly when discovery is instant)."""
    if result["type"] is FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(result["flow_id"])
    return result


async def test_user_flow_with_discovery(hass: HomeAssistant, free_port: int) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "user"

    with patch("custom_components.rehau_neasmart2.config_flow.async_discover", return_value=DETECTED):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NAME: "rehau", CONF_LISTEN_PORT: free_port}
        )
        result = await _run_progress(hass, result)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "topology"
    assert "1.1, 1.5" in result["description_placeholders"]["detected"]
    schema = {str(k): k.default() for k in result["data_schema"].schema if hasattr(k, "default")}
    assert schema[CONF_ZONES] == "1.1:Zone 1.1,1.5:Zone 1.5"
    assert schema[CONF_MIXED_GROUPS] == 1 and schema[CONF_PUMPS] == "1"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ZONES: "1.13:X", CONF_MIXED_GROUPS: 1}
    )
    assert result["errors"] == {CONF_ZONES: "invalid_zones"}

    with patch("custom_components.rehau_neasmart2.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_ZONES: "1.1:Living,1.5:Kitchen",
                CONF_MIXED_GROUPS: 1,
                CONF_PUMPS: "1",
                CONF_DEHUMIDIFIERS: "",
                CONF_RESTORE_GLOBALS: False,
                CONF_STALE_AFTER: 180,
            },
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_LISTEN_PORT] == free_port
    assert result["data"][CONF_ZONES] == "1.1:Living,1.5:Kitchen"
    assert result["result"].unique_id == "rehau"


async def test_user_flow_port_in_use(hass: HomeAssistant, free_port: int) -> None:
    blocker = await asyncio.start_server(lambda r, w: None, "0.0.0.0", free_port)
    try:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_NAME: "rehau", CONF_LISTEN_PORT: free_port}
        )
        assert result["errors"] == {CONF_LISTEN_PORT: "port_in_use"}
    finally:
        blocker.close()
        await blocker.wait_closed()


async def test_discovery_on_real_socket(free_port: int) -> None:
    async def play() -> None:
        await asyncio.sleep(0.2)
        conv = FakeConverter()
        await conv.connect(free_port)
        await conv.replay(load_capture())
        await asyncio.sleep(0.5)
        await conv.close()

    player = asyncio.create_task(play())
    found = await async_discover(free_port, max_seconds=10, settle_seconds=1)
    await player
    assert found.zones == [(1, 1), (1, 5), (1, 6), (1, 9), (1, 10), (1, 11)]
    assert found.mixed_groups == [1] and found.pumps == [1]


async def test_options_flow(hass: HomeAssistant, free_port: int) -> None:
    from .test_init import _entry

    entry = _entry(free_port)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LISTEN_PORT: free_port,
            CONF_ZONES: "1.1:Basement",
            CONF_MIXED_GROUPS: 0,
            CONF_PUMPS: "1,2",
            CONF_DEHUMIDIFIERS: "",
            CONF_RESTORE_GLOBALS: True,
            CONF_STALE_AFTER: 120,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()  # entry reloads with the new options
    hub = entry.runtime_data
    assert [z.name for z in hub.zones] == ["Basement"]
    assert hub.pumps == [1, 2] and hub.restore_globals and hub.stale_after == 120
    assert await hass.config_entries.async_unload(entry.entry_id)
