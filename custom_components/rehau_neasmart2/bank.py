"""Register bank of the virtual gateway (no Home Assistant imports; testable on its own).

The NEA SMART 2.0 base is the SYSBUS master. It broadcasts measurements (unit 0, FC16)
and polls the command registers (FC3, units 240/241, roughly every 2 s per zone) - and
it APPLIES whatever it reads. Therefore:

* stored command values are never served after a restart: serving 0 ("no data") makes
  the base republish its own values;
* a command from Home Assistant stays pending until the base has read it, and older
  broadcasts for that register are ignored in the meantime.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable

from .const import (
    MAX_BASES,
    MAX_ZONES_PER_BASE,
    REG_GLOBAL_MODE,
    REG_GLOBAL_STATE,
    ZONE_SETPOINT,
    ZONE_STATE,
    zone_base_reg,
)

_LOGGER = logging.getLogger(__name__)

GLOBAL_REGISTERS = frozenset({REG_GLOBAL_MODE, REG_GLOBAL_STATE})


def _command_registers() -> frozenset[int]:
    regs = set(GLOBAL_REGISTERS)
    for base in range(1, MAX_BASES + 1):
        for zone in range(1, MAX_ZONES_PER_BASE + 1):
            n = zone_base_reg(base, zone)
            regs.update({n + ZONE_STATE, n + ZONE_SETPOINT})
    return frozenset(regs)


COMMAND_REGISTERS = _command_registers()


class RegisterBank:
    """Registers served to the base, plus a display cache that survives restarts."""

    def __init__(self, on_change: Callable[[set[int]], None] | None = None) -> None:
        self._served: dict[int, int] = {}
        self._cached: dict[int, int] = {}  # known command values that are NOT served
        self._pending: dict[int, tuple[int, float, asyncio.Future[bool]]] = {}
        self._on_change = on_change
        self.written: set[int] = set()  # registers the base wrote since start
        self.last_bus_write: float | None = None  # time.monotonic()
        self.last_bus_read: float | None = None
        self.ignored_stale_writes = 0

    # ------------------------------------------------------------ persistence
    def load(self, cache: dict[int, int], serve_globals: bool) -> None:
        """Restore stored values.

        Zone command registers are NOT served (they stay 0): if someone changed a
        thermostat while Home Assistant was down, serving the old value would undo it.
        Global mode/state (1/2) are served only if `serve_globals` - the base hardly ever
        republishes them. Either way the stored values are still shown in Home Assistant.
        """
        for reg, value in cache.items():
            if reg in COMMAND_REGISTERS and not (serve_globals and reg in GLOBAL_REGISTERS):
                self._cached[reg] = value
            else:
                self._served[reg] = value

    def dump(self) -> dict[int, int]:
        out = dict(self._cached)
        out.update({r: v for r, v in self._served.items() if v})
        return out

    # ------------------------------------------------------------ HA side: read
    def get(self, reg: int) -> int | None:
        """Value to display: the served one; if it is 0 and a cached one exists, that."""
        value = self._served.get(reg)
        if not value and reg in self._cached:
            return self._cached[reg]
        return value

    def age(self) -> float | None:
        """Seconds since the base last wrote anything."""
        if self.last_bus_write is None:
            return None
        return time.monotonic() - self.last_bus_write

    # ------------------------------------------------------------ bus side
    def bus_write(self, addr: int, values: Iterable[int]) -> None:
        now = time.monotonic()
        changed: set[int] = set()
        for offset, value in enumerate(values):
            reg = addr + offset
            self.written.add(reg)
            pending = self._pending.get(reg)
            if pending is not None and pending[0] != value:
                # An older broadcast arriving before the base has read our command.
                self.ignored_stale_writes += 1
                _LOGGER.debug("Ignored base write %s=%s (pending %s)", reg, value, pending[0])
                continue
            if pending is not None:
                self._resolve(reg, True)
            if self.get(reg) != value:
                changed.add(reg)
            self._served[reg] = value
            self._cached.pop(reg, None)
        self.last_bus_write = now
        if changed and self._on_change:
            self._on_change(changed)

    def bus_read(self, addr: int, count: int) -> list[int]:
        self.last_bus_read = time.monotonic()
        for reg in range(addr, addr + count):
            if reg in self._pending:
                self._resolve(reg, True)
        return [self._served.get(reg, 0) for reg in range(addr, addr + count)]

    # ------------------------------------------------------------ HA side: commands
    def command(self, reg: int, value: int) -> asyncio.Future[bool]:
        """Write a command register; the future resolves (True) once the base reads it."""
        self.cancel_pending(reg)
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self._pending[reg] = (value, time.monotonic(), future)
        changed = self.get(reg) != value
        self._served[reg] = value
        self._cached.pop(reg, None)
        if changed and self._on_change:
            self._on_change({reg})
        return future

    def cancel_pending(self, reg: int) -> None:
        pending = self._pending.pop(reg, None)
        if pending and not pending[2].done():
            pending[2].set_result(False)

    def expire_pending(self, max_age: float) -> list[int]:
        """Drop commands the base has not read within `max_age` seconds."""
        now = time.monotonic()
        expired = [r for r, (_, ts, _) in self._pending.items() if now - ts > max_age]
        for reg in expired:
            self.cancel_pending(reg)
        return expired

    def pending(self) -> dict[int, int]:
        return {r: v for r, (v, _, _) in self._pending.items()}

    def _resolve(self, reg: int, ok: bool) -> None:
        pending = self._pending.pop(reg, None)
        if pending and not pending[2].done():
            pending[2].set_result(ok)
