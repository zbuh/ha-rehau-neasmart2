"""Rehau NEA SMART 2.0: direct SYSBUS integration (replaces the REST add-on)."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from .const import CONF_LISTEN_PORT, MIGRATION_LISTEN_PORT
from .hub import RehauHub

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SENSOR,
]

type RehauConfigEntry = ConfigEntry[RehauHub]


async def async_setup_entry(hass: HomeAssistant, entry: RehauConfigEntry) -> bool:
    """Start the SYSBUS server and set up the platforms."""
    hub = RehauHub(hass, entry)
    try:
        await hub.async_start()
    except HomeAssistantError as err:
        raise ConfigEntryNotReady(str(err)) from err
    entry.runtime_data = hub
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: RehauConfigEntry) -> bool:
    """Unload platforms, stop the server and save the register cache."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: RehauConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """1.1 (REST add-on, v0.3) -> 1.2 (own SYSBUS server).

    The major version stays 1 and the add-on host/port keys are kept, so v0.3 still
    accepts this entry if a user needs to roll back.
    """
    if entry.version > 1:
        return False
    if entry.minor_version < 2:
        data = {**entry.data, CONF_LISTEN_PORT: MIGRATION_LISTEN_PORT}
        hass.config_entries.async_update_entry(entry, data=data, minor_version=2)
        _LOGGER.warning(
            "Rehau NEA SMART 2.0: migrated from the REST add-on. The integration now listens "
            "on port %s; point the RS485 converter (TCP client) at this host and port. "
            "Stop the add-on once everything works",
            MIGRATION_LISTEN_PORT,
        )
    return True
