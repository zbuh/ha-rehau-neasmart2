# Changelog

## 1.0.0

First release of the direct-SYSBUS version. Replaces the REST add-on + integration v0.3.

- Built-in Modbus TCP slave (units 240/241 + broadcast); no add-on, no REST, no SQLite.
- Push updates; commands confirmed by the base's read (or an error after 15 s).
- Zone command registers never replayed after a restart; optional re-send of global mode/state.
- Pending-command protection against older broadcasts.
- Setup flow listens on the bus and pre-fills zones, mixed circuits, pumps and dehumidifiers.
- Options flow (port, topology, staleness, global restore) showing what is seen on the bus.
- Error/warning/hint flags read from registers 3/4/5 (the base never writes 6).
- New: SYSBUS connection binary sensor, extra pumps, dehumidifiers, diagnostic register N+3, diagnostics download.
- Climate entity supports the `native_*` API of Home Assistant 2026.11 and the older one.
- Translations: English, Portuguese, Italian.
- Automatic migration of v0.3 config entries (same unique IDs and entity IDs; listens on 5020 until the add-on is removed).
