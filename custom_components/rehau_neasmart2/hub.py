"""Hub: SYSBUS server + register bank + persistence + entity notifications."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later, async_track_time_interval
from homeassistant.helpers.storage import Store

from .bank import RegisterBank
from .const import (
    COMMAND_CONFIRM_TIMEOUT,
    CONF_DEHUMIDIFIERS,
    CONF_LISTEN_PORT,
    CONF_MIXED_GROUPS,
    CONF_NAME,
    CONF_PUMPS,
    CONF_RESTORE_GLOBALS,
    CONF_STALE_AFTER,
    CONF_ZONES,
    DEFAULT_LISTEN_PORT,
    DEFAULT_RESTORE_GLOBALS,
    DEFAULT_STALE_AFTER,
    DISCOVERY_MAX_SECONDS,
    DISCOVERY_SETTLE_SECONDS,
    DOMAIN,
    MAX_DEHUMIDIFIERS,
    MAX_PUMPS,
    SAVE_DELAY,
    SIGNAL_UPDATE,
)
from .server import SysbusServer
from .topology import Detected, detect, parse_index_list, parse_zone_entries

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
NOTIFY_DEBOUNCE = 0.5


@dataclass(frozen=True)
class Zone:
    base_id: int
    zone_id: int
    name: str


class RehauHub:
    """One NEA SMART 2.0 installation (master + slave bases) as seen on the SYSBUS."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        cfg = {**entry.data, **entry.options}
        self.id: str = cfg[CONF_NAME]
        self.name = f"{self.id} Climate Control System"
        self.port: int = int(cfg.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT))
        self.stale_after: int = int(cfg.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER))
        self.restore_globals: bool = bool(cfg.get(CONF_RESTORE_GLOBALS, DEFAULT_RESTORE_GLOBALS))
        self.zones = [Zone(b, z, n) for b, z, n in parse_zone_entries(cfg[CONF_ZONES])]
        self.mixed_groups = list(range(1, int(cfg.get(CONF_MIXED_GROUPS) or 0) + 1))
        self.pumps = parse_index_list(cfg.get(CONF_PUMPS, ""), MAX_PUMPS)
        self.dehumidifiers = parse_index_list(cfg.get(CONF_DEHUMIDIFIERS, ""), MAX_DEHUMIDIFIERS)

        self.bank = RegisterBank(on_change=self._on_bank_change)
        self.server = SysbusServer(self.bank, "0.0.0.0", self.port)
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._signal = SIGNAL_UPDATE.format(entry.entry_id)
        self._notify_handle: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._was_available: bool | None = None

    # ------------------------------------------------------------ lifecycle
    async def async_start(self) -> None:
        stored = await self._store.async_load() or {}
        cache = {int(k): int(v) for k, v in stored.get("registers", {}).items()}
        self.bank.load(cache, serve_globals=self.restore_globals)
        saved_at = stored.get("saved_at")
        if cache and saved_at and 0 <= time.time() - saved_at < self.stale_after:
            # Quick Home Assistant restart: the stored values are still fresh.
            self.bank.last_bus_write = time.monotonic() - (time.time() - saved_at)
        try:
            await self.server.start()
        except OSError as err:
            raise HomeAssistantError(f"Cannot listen on port {self.port}: {err}") from err
        self._unsubs.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=10)))

    async def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._notify_handle:
            self._notify_handle()
            self._notify_handle = None
        await self.server.stop()
        await self._store.async_save(self._data_to_save())

    # ------------------------------------------------------------ state
    @property
    def available(self) -> bool:
        age = self.bank.age()
        return age is not None and age <= self.stale_after

    def raw(self, reg: int) -> int | None:
        return self.bank.get(reg)

    def detected(self) -> Detected:
        return detect(self.bank)

    # ------------------------------------------------------------ commands
    async def async_command(self, reg: int, value: int, what: str) -> None:
        """Send a command and wait until the base has read it (about 2 s)."""
        if not self.server.connected:
            raise HomeAssistantError(f"{what}: the RS485 converter is not connected")
        future = self.bank.command(reg, value)
        self._schedule_save()
        try:
            async with asyncio.timeout(COMMAND_CONFIRM_TIMEOUT):
                ok = await future
        except TimeoutError:
            self.bank.cancel_pending(reg)
            raise HomeAssistantError(
                f"{what}: the REHAU base did not read the command within {COMMAND_CONFIRM_TIMEOUT} s"
            ) from None
        if not ok:
            raise HomeAssistantError(f"{what}: superseded by a newer command")

    # ------------------------------------------------------------ internals
    @callback
    def _on_bank_change(self, _regs: set[int]) -> None:
        self._schedule_save()
        if self._notify_handle is None:
            self._notify_handle = async_call_later(self.hass, NOTIFY_DEBOUNCE, self._notify)

    @callback
    def _notify(self, _now: Any = None) -> None:
        self._notify_handle = None
        self._was_available = self.available
        async_dispatcher_send(self.hass, self._signal)

    @callback
    def _tick(self, _now: Any) -> None:
        self.bank.expire_pending(COMMAND_CONFIRM_TIMEOUT * 2)
        if self.available != self._was_available:
            if not self.available and self._was_available:
                _LOGGER.warning(
                    "SYSBUS: no data from the base for more than %s s (converter connected: %s)",
                    self.stale_after,
                    self.server.connected,
                )
            self._notify()

    def _schedule_save(self) -> None:
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY)

    def _data_to_save(self) -> dict[str, Any]:
        return {
            "saved_at": time.time(),
            "registers": {str(k): v for k, v in self.bank.dump().items()},
        }

    def diagnostics(self) -> dict[str, Any]:
        detected = self.detected()
        return {
            "port": self.port,
            "available": self.available,
            "connected": self.server.connected,
            "peers": self.server.peers,
            "last_bus_write_age_s": self.bank.age(),
            "counters": dict(self.server.counters),
            "ignored_stale_writes": self.bank.ignored_stale_writes,
            "pending": self.bank.pending(),
            "detected": {
                "zones": [f"{b}.{z}" for b, z in detected.zones],
                "mixed_groups": detected.mixed_groups,
                "pumps": detected.pumps,
                "dehumidifiers": detected.dehumidifiers,
            },
            "registers": dict(sorted(self.bank.dump().items())),
        }


async def async_discover(
    port: int,
    max_seconds: float = DISCOVERY_MAX_SECONDS,
    settle_seconds: float = DISCOVERY_SETTLE_SECONDS,
) -> Detected:
    """Listen on `port` until the base has broadcast for `settle_seconds` (or time out)."""
    bank = RegisterBank()
    server = SysbusServer(bank, "0.0.0.0", port)
    await server.start()
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_seconds
        first: float | None = None
        while loop.time() < deadline:
            await asyncio.sleep(0.5)
            if first is None and bank.last_bus_write is not None:
                first = loop.time()
            if first is not None and loop.time() - first >= settle_seconds:
                break
        return detect(bank)
    finally:
        await server.stop()
