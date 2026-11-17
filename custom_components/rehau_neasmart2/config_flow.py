"""Config flow: listen on the bus, detect the installation, confirm the topology."""

from __future__ import annotations

import asyncio
import logging
import socket
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback

from .const import (
    CONF_DEHUMIDIFIERS,
    CONF_LISTEN_PORT,
    CONF_MIXED_GROUPS,
    CONF_NAME,
    CONF_PUMPS,
    CONF_RESTORE_GLOBALS,
    CONF_STALE_AFTER,
    CONF_ZONES,
    DEFAULT_LISTEN_PORT,
    DEFAULT_NAME,
    DEFAULT_RESTORE_GLOBALS,
    DEFAULT_STALE_AFTER,
    DOMAIN,
    MAX_DEHUMIDIFIERS,
    MAX_MIXED_GROUPS,
    MAX_PUMPS,
)
from .hub import async_discover
from .topology import Detected, parse_index_list, parse_zone_entries

_LOGGER = logging.getLogger(__name__)

PORT = vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))


def _topology_schema(d: dict[str, Any]) -> dict:
    return {
        vol.Required(CONF_ZONES, default=d.get(CONF_ZONES, "")): str,
        vol.Optional(CONF_MIXED_GROUPS, default=d.get(CONF_MIXED_GROUPS) or 0): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=MAX_MIXED_GROUPS)
        ),
        vol.Optional(CONF_PUMPS, default=d.get(CONF_PUMPS, "")): str,
        vol.Optional(CONF_DEHUMIDIFIERS, default=d.get(CONF_DEHUMIDIFIERS, "")): str,
        vol.Optional(
            CONF_RESTORE_GLOBALS, default=d.get(CONF_RESTORE_GLOBALS, DEFAULT_RESTORE_GLOBALS)
        ): bool,
        vol.Optional(CONF_STALE_AFTER, default=d.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER)): vol.All(
            vol.Coerce(int), vol.Range(min=30, max=3600)
        ),
    }


def _validate_topology(data: dict[str, Any]) -> dict[str, str]:
    errors: dict[str, str] = {}
    try:
        parse_zone_entries(data[CONF_ZONES])
    except ValueError:
        errors[CONF_ZONES] = "invalid_zones"
    try:
        parse_index_list(data.get(CONF_PUMPS, ""), MAX_PUMPS)
    except ValueError:
        errors[CONF_PUMPS] = "invalid_index"
    try:
        parse_index_list(data.get(CONF_DEHUMIDIFIERS, ""), MAX_DEHUMIDIFIERS)
    except ValueError:
        errors[CONF_DEHUMIDIFIERS] = "invalid_index"
    return errors


def port_free(port: int) -> bool:
    """True if nothing listens on the TCP port (e.g. the old add-on)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
        except OSError:
            return False
    return True


def _detected_defaults(found: Detected) -> dict[str, Any]:
    return {
        CONF_ZONES: found.zones_config(),
        CONF_MIXED_GROUPS: max(found.mixed_groups, default=0),
        CONF_PUMPS: ",".join(map(str, found.pumps)),
        CONF_DEHUMIDIFIERS: ",".join(map(str, found.dehumidifiers)),
    }


def _detected_text(found: Detected | None) -> str:
    if found is None or not (found.zones or found.mixed_groups or found.pumps):
        return "nothing"
    parts = [f"zones {', '.join(f'{b}.{z}' for b, z in found.zones) or '-'}"]
    if found.mixed_groups:
        parts.append(f"mixed groups {', '.join(map(str, found.mixed_groups))}")
    if found.pumps:
        parts.append(f"pumps {', '.join(map(str, found.pumps))}")
    if found.dehumidifiers:
        parts.append(f"dehumidifiers {', '.join(map(str, found.dehumidifiers))}")
    return "; ".join(parts)


class RehauConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Rehau NEA SMART 2.0."""

    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        self._user: dict[str, Any] = {}
        self._task: asyncio.Task[Detected] | None = None
        self._detected: Detected | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_NAME])
            self._abort_if_unique_id_configured()
            if not await self.hass.async_add_executor_job(port_free, user_input[CONF_LISTEN_PORT]):
                errors[CONF_LISTEN_PORT] = "port_in_use"
            else:
                self._user = user_input
                return await self.async_step_discover()
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_LISTEN_PORT, default=DEFAULT_LISTEN_PORT): PORT,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_discover(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._task is None:
            self._task = self.hass.async_create_task(async_discover(self._user[CONF_LISTEN_PORT]))
        if not self._task.done():
            return self.async_show_progress(
                step_id="discover",
                progress_action="discover",
                progress_task=self._task,
                description_placeholders={"port": str(self._user[CONF_LISTEN_PORT])},
            )
        try:
            self._detected = self._task.result()
        except OSError:
            _LOGGER.exception("Discovery failed")
            self._detected = None
        return self.async_show_progress_done(next_step_id="topology")

    async def async_step_topology(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input and CONF_ZONES in user_input:  # not the hand-over from the progress step
            errors = _validate_topology(user_input)
            if not errors:
                return self.async_create_entry(
                    title=f"{self._user[CONF_NAME]} Climate Control System",
                    data={**self._user, **user_input},
                )
        defaults = (user_input if user_input and CONF_ZONES in user_input else None) or (
            _detected_defaults(self._detected) if self._detected else {}
        )
        return self.async_show_form(
            step_id="topology",
            data_schema=vol.Schema(_topology_schema(defaults)),
            errors=errors,
            description_placeholders={"detected": _detected_text(self._detected)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return RehauOptionsFlow()


class RehauOptionsFlow(OptionsFlow):
    """Change port and topology; shows what is currently seen on the bus."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        current = {**self.config_entry.data, **self.config_entry.options}
        current_port = int(current.get(CONF_LISTEN_PORT, DEFAULT_LISTEN_PORT))
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = _validate_topology(user_input)
            port = user_input[CONF_LISTEN_PORT]
            if port != current_port and not await self.hass.async_add_executor_job(port_free, port):
                errors[CONF_LISTEN_PORT] = "port_in_use"
            if not errors:
                return self.async_create_entry(data=user_input)
        hub = getattr(self.config_entry, "runtime_data", None)
        detected = hub.detected() if hub is not None else None
        schema = {vol.Required(CONF_LISTEN_PORT, default=current_port): PORT}
        schema.update(_topology_schema(user_input or current))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(schema),
            errors=errors,
            description_placeholders={"detected": _detected_text(detected)},
        )
