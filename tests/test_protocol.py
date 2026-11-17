"""Protocol core (no Home Assistant): DPT 9, register bank, server, discovery."""

from __future__ import annotations

import asyncio
import struct

import pytest

from custom_components.rehau_neasmart2 import dpt9
from custom_components.rehau_neasmart2.bank import RegisterBank
from custom_components.rehau_neasmart2.server import SysbusServer
from custom_components.rehau_neasmart2.topology import (
    detect,
    parse_index_list,
    parse_zone_entries,
)

from .helpers import FakeConverter, load_capture, pdu, read_holding, write_multiple


@pytest.mark.parametrize("value", [23.0, 22.5, 19.0, -3.2, 30.4, 17.9, 0.0, -20.0, 45.0])
def test_dpt9_roundtrip(value: float) -> None:
    assert dpt9.unpack(dpt9.pack(value)) == value


def test_dpt9_known_values_and_sentinels() -> None:
    assert dpt9.pack(23.0) == 3198 == 0x0C7E
    assert dpt9.unpack(0x86C0) == -3.2
    assert dpt9.temperature(0x8116) is None  # standby setpoint
    assert dpt9.temperature(0x7FFF) is None  # not initialised
    assert dpt9.temperature(None) is None


def _replay(bank: RegisterBank) -> SysbusServer:
    server = SysbusServer(bank, "127.0.0.1", 0)
    for rec in load_capture():
        server.handle(rec["u"], pdu(rec))
    return server


async def test_replay_real_capture() -> None:
    bank = RegisterBank()
    server = _replay(bank)
    assert server.counters["other_unit"] == 200  # proprietary unit-128 frames ignored
    last: dict[int, int] = {}
    for rec in load_capture():
        if rec["u"] == 0 and rec["fc"] == 16:
            for i, v in enumerate(rec["v"]):
                last[rec["a"] + i] = v
    assert {a: bank.get(a) for a in last} == last
    assert bank.get(100) == 1 and dpt9.temperature(bank.get(101)) == 23.0


async def test_detect_topology_from_capture() -> None:
    bank = RegisterBank()
    _replay(bank)
    found = detect(bank)
    assert found.zones == [(1, 1), (1, 5), (1, 6), (1, 9), (1, 10), (1, 11)]
    assert found.mixed_groups == [1]
    assert found.pumps == [1]
    assert found.dehumidifiers == []
    assert found.zones_config().startswith("1.1:Zone 1.1,1.5:Zone 1.5")


async def test_command_pending_until_read_and_stale_broadcast_ignored() -> None:
    bank = RegisterBank()
    server = SysbusServer(bank, "127.0.0.1", 0)
    bank.bus_write(100, [1, 3198])
    future = bank.command(101, dpt9.pack(22.5))
    # an older broadcast for the same register must not overwrite the command
    server.handle(0, write_multiple(101, [3198]))
    assert bank.get(101) == dpt9.pack(22.5)
    assert bank.ignored_stale_writes == 1
    response = server.handle(240, read_holding(100, 2))
    assert struct.unpack(">HH", response[2:]) == (1, dpt9.pack(22.5))
    assert future.result() is True
    # once read, a change made on the thermostat is accepted again
    server.handle(0, write_multiple(101, [3223]))
    assert bank.get(101) == 3223


async def test_restart_never_serves_zone_commands() -> None:
    bank = RegisterBank()
    bank.bus_write(100, [1, 3198])
    bank.command(1, 4)
    bank.command(2, 1)
    cache = bank.dump()

    restored = RegisterBank()
    restored.load(cache, serve_globals=False)
    assert restored.bus_read(100, 2) == [0, 0]  # base reads "no data" and republishes
    assert restored.bus_read(1, 2) == [0, 0]
    assert (restored.get(100), restored.get(101), restored.get(1)) == (1, 3198, 4)  # shown

    with_globals = RegisterBank()
    with_globals.load(cache, serve_globals=True)
    assert with_globals.bus_read(1, 2) == [4, 1]
    assert with_globals.bus_read(100, 2) == [0, 0]
    with_globals.bus_write(100, [2])  # base republishes
    assert with_globals.bus_read(100, 1) == [2]


async def test_expire_pending() -> None:
    bank = RegisterBank()
    future = bank.command(500, 3)
    assert bank.expire_pending(-1) == [500]
    assert future.result() is False


async def test_server_over_tcp(free_port: int) -> None:
    bank = RegisterBank()
    bank.bus_write(1, [4, 1])
    server = SysbusServer(bank, "127.0.0.1", free_port)
    await server.start()
    conv = FakeConverter()
    await conv.connect(free_port)
    try:
        # unit 128 is ignored, unit 241 answered
        await conv.send(128, bytes.fromhex("00802000"))
        assert await conv.request(241, read_holding(1, 2)) == bytes([3, 4]) + struct.pack(">HH", 4, 1)
        # Waveshare forwards proprietary frames with a non-zero protocol id
        await conv.send(128, bytes(29), pid=0x010C)
        assert await conv.request(240, read_holding(1, 1)) == bytes([3, 2]) + struct.pack(">H", 4)
        assert server.connected
        # impossible length: framing lost, server closes the connection
        assert conv.writer is not None and conv.reader is not None
        conv.writer.write(bytes(7))
        await conv.writer.drain()
        assert await asyncio.wait_for(conv.reader.read(16), 2) == b""
    finally:
        await conv.close()
        await server.stop()
    assert server.counters["bad_header"] == 1


def test_parse_config() -> None:
    assert parse_zone_entries("1.1:Living, 2.3:Kitchen") == [(1, 1, "Living"), (2, 3, "Kitchen")]
    assert parse_zone_entries("A,B") == [(1, 1, "A"), (1, 2, "B")]  # legacy positional form
    for bad in ("", "1.13:X", "6.1:X", "1.1:A,1.1:B", "x.1:A", "1.1:"):
        with pytest.raises(ValueError):
            parse_zone_entries(bad)
    assert parse_index_list("1, 3", 5) == [1, 3]
    assert parse_index_list("", 5) == []
    for bad in ("0", "6", "1,1", "a"):
        with pytest.raises(ValueError):
            parse_index_list(bad, 5)
