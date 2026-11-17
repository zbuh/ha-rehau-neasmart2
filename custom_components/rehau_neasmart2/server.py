"""Minimal Modbus TCP slave for the REHAU SYSBUS, reached through an RS485-to-TCP converter.

Only MBAP framing and FC3/FC6/FC16. Answers units 240/241, accepts broadcast writes
(unit 0) and silently ignores everything else (proprietary traffic between bases,
"unit 128" frames, ...). No dependencies, so no clash with the pymodbus version that
Home Assistant core pins.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from collections import Counter

from .bank import RegisterBank
from .const import BROADCAST_UNIT, GATEWAY_UNITS

_LOGGER = logging.getLogger(__name__)

MAX_PDU = 253
EXC_ILLEGAL_FUNCTION = 1
EXC_ILLEGAL_VALUE = 3


class SysbusServer:
    """asyncio TCP server; the converter connects to it as a TCP client."""

    def __init__(self, bank: RegisterBank, host: str, port: int) -> None:
        self.bank = bank
        self.host = host
        self.port = port
        self._server: asyncio.base_events.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self.counters: Counter[str] = Counter()
        self.peers: list[str] = []

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._client, self.host, self.port, reuse_address=True)
        _LOGGER.info("SYSBUS: listening on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
        for writer in list(self._clients):
            writer.close()
        if self._server is not None:
            with contextlib.suppress(Exception):
                await self._server.wait_closed()
        self._server = None

    @property
    def connected(self) -> bool:
        return bool(self._clients)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        peer_s = f"{peer[0]}:{peer[1]}" if peer else "?"
        self._clients.add(writer)
        self.peers = [*self.peers[-4:], peer_s]
        self.counters["connections"] += 1
        _LOGGER.info("SYSBUS: converter connected (%s)", peer_s)
        try:
            while True:
                header = await reader.readexactly(7)
                tid, pid, length, unit = struct.unpack(">HHHB", header)
                # Waveshare converters forward proprietary frames with a non-zero
                # protocol id: only the length is used to stay in sync.
                if not 2 <= length <= MAX_PDU + 1:
                    self.counters["bad_header"] += 1
                    _LOGGER.debug("SYSBUS: invalid MBAP header %s", header.hex())
                    break  # framing lost: close, the converter reconnects
                pdu = await reader.readexactly(length - 1)
                response = self.handle(unit, pdu)
                if response is not None:
                    writer.write(struct.pack(">HHHB", tid, pid, len(response) + 1, unit) + response)
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError, OSError) as err:
            _LOGGER.debug("SYSBUS: connection %s ended: %s", peer_s, err)
        finally:
            self._clients.discard(writer)
            writer.close()
            _LOGGER.info("SYSBUS: converter disconnected (%s)", peer_s)

    def handle(self, unit: int, pdu: bytes) -> bytes | None:
        """Process one PDU and return the response PDU, or None for no response."""
        ours = unit in GATEWAY_UNITS
        if not ours and unit != BROADCAST_UNIT:
            self.counters["other_unit"] += 1
            return None
        fc = pdu[0]
        self.counters[f"fc{fc}"] += 1
        try:
            if fc == 3:
                addr, count = struct.unpack(">HH", pdu[1:5])
                if not ours:
                    return None
                if not 1 <= count <= 125:
                    return bytes([fc | 0x80, EXC_ILLEGAL_VALUE])
                values = self.bank.bus_read(addr, count)
                return bytes([3, 2 * count]) + struct.pack(f">{count}H", *values)
            if fc == 6:
                addr, value = struct.unpack(">HH", pdu[1:5])
                self.bank.bus_write(addr, [value])
                return pdu[:5] if ours else None
            if fc == 16:
                addr, count, nbytes = struct.unpack(">HHB", pdu[1:6])
                if nbytes != 2 * count or len(pdu) < 6 + nbytes:
                    self.counters["malformed"] += 1
                    return bytes([fc | 0x80, EXC_ILLEGAL_VALUE]) if ours else None
                values = struct.unpack(f">{count}H", pdu[6 : 6 + nbytes])
                self.bank.bus_write(addr, values)
                return pdu[:5] if ours else None
        except struct.error:
            self.counters["malformed"] += 1
            return bytes([fc | 0x80, EXC_ILLEGAL_VALUE]) if ours else None
        self.counters["unsupported_fc"] += 1
        return bytes([fc | 0x80, EXC_ILLEGAL_FUNCTION]) if ours else None
