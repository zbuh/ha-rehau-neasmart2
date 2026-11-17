"""Shared helpers: replay of a real SYSBUS capture and a fake converter client."""

from __future__ import annotations

import asyncio
import json
import struct
from pathlib import Path

CAPTURE = Path(__file__).parent / "fixtures" / "capture.jsonl"


def load_capture() -> list[dict]:
    """Frames captured from a real installation (1 base, 6 zones, 1 mixed group).

    Each record: u=unit, fc=function, a=address, n=count (reads), v=values (writes),
    raw=hex PDU (proprietary frames), t=seconds since start.
    """
    return [json.loads(line) for line in CAPTURE.read_text().splitlines() if line.strip()]


def pdu(rec: dict) -> bytes:
    """Rebuild the Modbus PDU of a captured record."""
    if rec["fc"] == 3:
        return bytes([3]) + struct.pack(">HH", rec["a"], rec["n"])
    if rec["fc"] == 6:
        return bytes([6]) + struct.pack(">HH", rec["a"], rec["v"][0])
    if rec["fc"] == 16:
        values = rec["v"]
        return (
            bytes([16])
            + struct.pack(">HHB", rec["a"], len(values), 2 * len(values))
            + struct.pack(f">{len(values)}H", *values)
        )
    return bytes([rec["fc"]]) + bytes.fromhex(rec.get("raw", ""))[1:]


def write_multiple(addr: int, values: list[int]) -> bytes:
    return (
        bytes([16])
        + struct.pack(">HHB", addr, len(values), 2 * len(values))
        + struct.pack(f">{len(values)}H", *values)
    )


def read_holding(addr: int, count: int) -> bytes:
    return bytes([3]) + struct.pack(">HH", addr, count)


class FakeConverter:
    """Plays the REHAU base behind a Waveshare converter (TCP client, MBAP framing)."""

    def __init__(self) -> None:
        self.reader: asyncio.StreamReader | None = None
        self.writer: asyncio.StreamWriter | None = None
        self._tid = 0

    async def connect(self, port: int) -> None:
        self.reader, self.writer = await asyncio.open_connection("127.0.0.1", port)

    async def send(self, unit: int, payload: bytes, pid: int = 0) -> None:
        self._tid = (self._tid + 1) & 0xFFFF
        assert self.writer is not None
        self.writer.write(struct.pack(">HHHB", self._tid, pid, len(payload) + 1, unit) + payload)
        await self.writer.drain()

    async def request(self, unit: int, payload: bytes) -> bytes:
        """Send and wait for the response PDU."""
        await self.send(unit, payload)
        assert self.reader is not None
        header = await asyncio.wait_for(self.reader.readexactly(7), 2)
        _, _, length, _ = struct.unpack(">HHHB", header)
        return await asyncio.wait_for(self.reader.readexactly(length - 1), 2)

    async def replay(self, records: list[dict]) -> None:
        """Send every captured frame; read responses of FC3 requests to units 240/241."""
        for rec in records:
            if rec["u"] in (240, 241) and rec["fc"] == 3:
                await self.request(rec["u"], pdu(rec))
            else:
                await self.send(rec["u"], pdu(rec))

    async def close(self) -> None:
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
