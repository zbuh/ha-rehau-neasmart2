#!/usr/bin/env python3
"""Capture REHAU NEA SMART 2.0 SYSBUS traffic through an RS485-to-TCP converter.

Acts as the KNX gateway (slave 240/241) like the integration does, answers the base's
reads and logs EVERY frame (including other unit IDs) as JSON lines. Use it to look for
undocumented registers: point the converter at this machine, leave it idle for a few
minutes, then change things on a thermostat and note the times.

While capturing, Home Assistant cannot control the system. To answer the base with the
same command values Home Assistant currently uses, pass them with --mode/--state/--zone
(otherwise 0 is served, which the base treats as "no data").

Usage:
  python3 sysbus_capture.py --port 5020 --mode 4 --state 1 --zone 1.1=1:23.0
  python3 sysbus_capture.py --report capture-*.jsonl

Standard library only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import struct
import time
from collections import Counter, defaultdict
from pathlib import Path

GATEWAY_UNITS = {240, 241}
BROADCAST = 0


def pack_dpt9(value: float) -> int:
    mantissa, exponent = int(round(value * 100)), 0
    while mantissa > 2047 or mantissa < -2048:
        mantissa //= 2
        exponent += 1
    sign = 0
    if mantissa < 0:
        sign, mantissa = 1, mantissa + 2048
    return (sign << 15) | (exponent << 11) | (mantissa & 0x7FF)


def unpack_dpt9(raw: int) -> float | None:
    if raw in (0x7FFF, 0x8116):
        return None
    mantissa = raw & 0x7FF
    if raw & 0x8000:
        mantissa -= 2048
    return round(0.01 * mantissa * (1 << ((raw >> 11) & 0xF)), 2)


def documented(addr: int) -> str | None:
    """Name of a register in the REHAU KNX gateway datapoint list."""
    fixed = {
        1: "global mode",
        2: "global state",
        3: "error",
        4: "warning?",
        5: "hint?",
        6: "hint (manual)",
        7: "outside temp",
        8: "outside temp filtered",
    }
    if addr in fixed:
        return fixed[addr]
    if 10 <= addr <= 21:
        group, offset = divmod(addr - 10, 4)
        return f"mixed group {group + 1} " + ["valve %", "pump", "flow", "return"][offset]
    if 22 <= addr <= 30:
        return f"dehumidifier {addr - 21}"
    if 31 <= addr <= 35:
        return f"pump {addr - 30}"
    if 100 <= addr < 6100:
        n, offset = divmod(addr, 100)
        base, zone = divmod(n - 1, 12)
        names = {0: "state", 1: "setpoint", 2: "temperature", 3: "undocumented N+3", 10: "humidity"}
        if offset in names:
            return f"zone {base + 1}.{zone + 1} {names[offset]}"
    return None


class Capture:
    def __init__(self, out: Path, seed: dict[int, int]) -> None:
        self.regs: dict[int, int] = defaultdict(int, seed)
        self.out = out.open("a", buffering=1)

    def log(self, **rec) -> None:
        rec["t"] = round(time.time(), 3)
        self.out.write(json.dumps(rec) + "\n")

    def handle(self, unit: int, pdu: bytes) -> bytes | None:
        fc = pdu[0]
        ours = unit in GATEWAY_UNITS or unit == BROADCAST
        try:
            if fc == 3 and len(pdu) >= 5:
                addr, count = struct.unpack(">HH", pdu[1:5])
                values = [self.regs[a] for a in range(addr, addr + count)]
                self.log(u=unit, fc=3, a=addr, n=count)
                if unit in GATEWAY_UNITS:
                    return bytes([3, 2 * count]) + struct.pack(f">{count}H", *values)
                return None
            if fc == 6 and len(pdu) >= 5:
                addr, value = struct.unpack(">HH", pdu[1:5])
                self.log(u=unit, fc=6, a=addr, v=[value])
                if ours:
                    self.regs[addr] = value
                return pdu[:5] if unit in GATEWAY_UNITS else None
            if fc == 16 and len(pdu) >= 6:
                addr, count, _ = struct.unpack(">HHB", pdu[1:6])
                values = list(struct.unpack(f">{count}H", pdu[6 : 6 + 2 * count]))
                self.log(u=unit, fc=16, a=addr, v=values)
                if ours:
                    for i, value in enumerate(values):
                        self.regs[addr + i] = value
                return pdu[:5] if unit in GATEWAY_UNITS else None
        except struct.error:
            pass
        self.log(u=unit, fc=fc, raw=pdu.hex())
        return bytes([fc | 0x80, 1]) if unit in GATEWAY_UNITS else None


async def serve(capture: Capture, host: str, port: int) -> None:
    async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        print("connected:", peer, flush=True)
        capture.log(ev="connect", peer=str(peer))
        try:
            while True:
                tid, pid, length, unit = struct.unpack(">HHHB", await reader.readexactly(7))
                pdu = await reader.readexactly(length - 1)
                response = capture.handle(unit, pdu)
                if response is not None:
                    writer.write(struct.pack(">HHHB", tid, pid, len(response) + 1, unit) + response)
                    await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError) as err:
            print("disconnected:", peer, err, flush=True)
            capture.log(ev="disconnect", peer=str(peer))
        finally:
            writer.close()

    server = await asyncio.start_server(client, host, port)
    print(f"listening on {host}:{port}", flush=True)
    async with server:
        await server.serve_forever()


def report(files: list[str]) -> None:
    frames = []
    for name in files:
        with open(name) as handle:
            frames += [json.loads(line) for line in handle if line.strip()]
    frames = [r for r in frames if "fc" in r]
    if not frames:
        print("no frames")
        return
    duration = max(frames[-1]["t"] - frames[0]["t"], 1)
    print(f"{len(frames)} frames in {duration:.0f} s ({len(frames) / duration:.1f}/s)\n")
    print("By unit / function:")
    for (unit, fc), count in sorted(Counter((r["u"], r["fc"]) for r in frames).items()):
        print(f"  unit {unit:3}  FC{fc:<3} {count:7}")

    writes: dict[int, list[tuple[float, int]]] = defaultdict(list)
    reads: Counter[int] = Counter()
    others: dict[int, Counter] = defaultdict(Counter)
    for r in frames:
        if r["u"] not in GATEWAY_UNITS and r["u"] != BROADCAST:
            others[r["u"]][(r["fc"], r.get("a"), r.get("raw", "")[:16])] += 1
        elif r["fc"] in (6, 16):
            for i, value in enumerate(r["v"]):
                writes[r["a"] + i].append((r["t"], value))
        elif r["fc"] == 3:
            for a in range(r["a"], r["a"] + r["n"]):
                reads[a] += 1

    print("\nRegisters WRITTEN by the base (changes with local time):")
    for addr in sorted(writes):
        print(f"  {addr:5}  {documented(addr) or '*** UNDOCUMENTED ***'}")
        last = None
        for t, value in writes[addr]:
            if value != last:
                when = time.strftime("%H:%M:%S", time.localtime(t))
                print(f"           {when}  {value:5}  (dpt9 {unpack_dpt9(value)})")
                last = value
    print("\nRegisters READ by the base:")
    for addr in sorted(reads):
        print(
            f"  {addr:5}  {documented(addr) or '*** UNDOCUMENTED ***':28} every ~{duration / reads[addr]:.1f}s"
        )
    if others:
        print("\nOther unit IDs (not answered):")
        for unit in sorted(others):
            for key, count in others[unit].most_common(10):
                print(f"  unit {unit}: {key} x{count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--mode", type=int, default=0, help="global mode to serve (1-5)")
    parser.add_argument("--state", type=int, default=0, help="global state to serve (1-6)")
    parser.add_argument(
        "--zone",
        action="append",
        default=[],
        metavar="B.Z=STATE:SETPOINT",
        help="zone command values to serve, e.g. 1.5=1:21.5 (repeatable)",
    )
    parser.add_argument("--report", nargs="+", metavar="FILE")
    args = parser.parse_args()
    if args.report:
        report(args.report)
        return
    seed = {1: args.mode, 2: args.state}
    for spec in args.zone:
        address, values = spec.split("=")
        base, zone = (int(x) for x in address.split("."))
        state, setpoint = values.split(":")
        n = (base - 1) * 1200 + zone * 100
        seed[n], seed[n + 1] = int(state), pack_dpt9(float(setpoint))
    out = Path.cwd() / time.strftime("capture-%Y%m%d-%H%M%S.jsonl")
    print("writing", out, flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(Capture(out, seed), args.host, args.port))


if __name__ == "__main__":
    main()
