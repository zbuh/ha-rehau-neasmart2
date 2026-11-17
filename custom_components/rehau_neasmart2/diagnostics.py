"""Diagnostics: registers, counters, detected topology and pending commands."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import RehauConfigEntry


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: RehauConfigEntry) -> dict[str, Any]:
    return {"config": {**entry.data, **entry.options}, "hub": entry.runtime_data.diagnostics()}
