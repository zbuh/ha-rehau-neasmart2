# Rehau NEA SMART 2.0 for Home Assistant (direct SYSBUS)

Local, push-based integration for the **REHAU NEA SMART 2.0** underfloor heating/cooling
controller. Home Assistant itself plays the role of REHAU's official *KNX gateway*
(a Modbus RTU slave on the base station's SYSBUS), reached through an inexpensive
RS485-to-Ethernet converter.

- **One component**: no add-on, no REST shim, no database. A tiny built-in Modbus TCP
  server runs inside Home Assistant.
- **Real time**: the base broadcasts measurements every ~10 s and they show up immediately.
- **Confirmed commands**: a setpoint/preset/mode change only succeeds once the base has
  actually read it (about 2 s); otherwise the action raises an error instead of failing silently.
- **Safe restarts**: stored commands are never replayed to the base (a change made on a
  thermostat while Home Assistant was down is not undone).
- **Auto-detection** of zones, mixed circuits, pumps and dehumidifiers during setup.
- **Drop-in replacement** for [MatteoManzoni's add-on + integration](https://github.com/MatteoManzoni/rehau-neasmart2.0-integration-ha):
  same domain, same unique IDs, so existing entity IDs, automations and dashboards keep working.

```
REHAU base ──SYSBUS (RS485)──> converter (TCP client) ──TCP──> Home Assistant :502
```

> The REHAU app and cloud keep working: the SYSBUS is independent of the base's Wi-Fi.

## What you get

| Entity | Notes |
|---|---|
| `climate` per zone | current temperature, humidity, target temperature, presets `Normal`, `Reduced`, `Standby`, `Time Program`, `Party`, `Absence` |
| `select` global climate mode | `Auto`, `Heating`, `Cooling`, `Forced Heating`, `Forced Cooling` (manual heating/cooling) |
| `select` global climate state | same presets as the zones, for the whole installation |
| `sensor` per zone | temperature, humidity; diagnostic *Register N+3* (disabled, see [protocol notes](docs/protocol.md)) |
| `sensor` per mixed circuit | valve opening %, pump, flow and return temperature |
| `sensor` | outside temperature (raw and filtered), errors, warnings, hints, extra pumps, dehumidifiers |
| `binary_sensor` SYSBUS connection | diagnostic: fresh data from the base; attributes show whether the converter is connected |

Option values and states (`Present`/`Not Present`, `On`/`Off`) are kept identical to v0.3
so existing automations keep working; the UI shows them translated (English, Portuguese, Italian).

**Not available** on the SYSBUS (only through REHAU's cloud or the base's access-point web pages):
thermostat LED ring, child lock, window detection, per-room valve/heat-demand state, schedules,
heating curve. See [docs/protocol.md](docs/protocol.md).

## Hardware

Any RS485-to-Ethernet converter that can act as a **TCP client** with **Modbus TCP ↔ RTU**
conversion works. Tested with the **Waveshare RS485 TO POE ETH (B)**.

Wiring to the base station's free **SYSBUS** terminals:

| NEA SMART 2.0 SYSBUS | Waveshare |
|---|---|
| GND | the **earth/ground symbol** terminal — *not* the "GND" of the power input |
| 1 | 485A |
| 2 | 485B |

> ⚠️ Work with the installation powered off. Connecting SYSBUS GND to the converter's power
> GND has destroyed at least one base station. Use twisted-pair cable (REHAU specifies
> J-Y(St)Y 2×2×0.8 mm, line topology, up to 500 m).

Converter settings (Waveshare: use the VirCOM tool; the web page does not expose everything):

| Setting | Value |
|---|---|
| Work mode | TCP Client |
| Destination IP / port | your Home Assistant host / `502` (or the port you choose) |
| Baud rate / data / parity / stop | **38400 / 8 / None / 1** |
| Transfer protocol | Modbus TCP Protocol ("Simple Modbus TCP to RTU") |
| Destination mode | Dynamic |

A step-by-step guide with screenshots is in the
[original add-on documentation](https://github.com/MatteoManzoni/RehauNeasmart2.0_Gateway/blob/main/rehau_neasmart2.0_gateway/waveshare_poegw_howto.md).

Home Assistant must accept TCP connections on the chosen port (HA OS: nothing to do;
Docker: publish the port or use host networking).

## Installation

### HACS
1. HACS → ⋮ → *Custom repositories* → add `https://github.com/zbuh/ha-rehau-neasmart2` (type *Integration*).
2. Install **Rehau NEA SMART 2.0** and restart Home Assistant.

### Manual
Copy `custom_components/rehau_neasmart2` into your `config/custom_components/` and restart.

Tested on Home Assistant 2026.10 and 2026.11; it should work from 2025.2 on.

## Setup

*Settings → Devices & services → Add integration → Rehau NEA SMART 2.0*

1. **System identifier** (used in unique IDs; keep `rehau` unless you have several systems)
   and **TCP port** (default 502).
2. The integration listens for up to 90 s. Point the converter at Home Assistant if you
   have not yet; it reconnects on its own.
3. Detected zones, mixed circuits, pumps and dehumidifiers are pre-filled. Rename the zones
   (`base.zone:name`, e.g. `1.5:Living room`) or later on each device page.

Options (*Configure* on the integration) let you change the port and topology, and show what
is currently seen on the bus.

| Option | Default | Meaning |
|---|---|---|
| Re-send last global mode/state after a restart | off | The base rarely republishes the global mode/state. Turn on only if Home Assistant is the only place where you change them, otherwise a change made in the REHAU app while HA was down would be undone. The last known values are always *shown*. |
| Unavailable after … seconds without data | 180 | When the base stops broadcasting, entities become unavailable. |

### Zone addresses
Zones are `base.zone`: base `1` is the master, `2`–`5` are slave bases **as numbered by their
DIP switches** (a base set as "slave 4" is base 5 even if it is the second one installed).
Zone `1`–`12` is the room channel; a room using several channels is controlled through its
lowest one (visible in the base's web pages in access-point mode).

## Migrating from the add-on (v0.3)

1. Install this integration over the old one (same domain) and restart.
   The config entry migrates automatically and the integration listens on **port 5020**,
   because the add-on still holds 502.
2. Point the converter at Home Assistant, port 5020.
3. Check the *SYSBUS connection* sensor and your thermostats.
4. Stop and uninstall the add-on. Optionally change the port to 502 in the integration
   options and in the converter.

Entity IDs, names and areas do not change. The first time, the global mode/state show
`unknown` until something writes them (the base does not broadcast them) — select the
current values once.

**Rollback**: reinstall v0.3 of the integration, restart, point the converter back to the
add-on (port 502) and start the add-on. The migrated entry stays compatible with v0.3.

## Behaviour worth knowing

- **Command confirmation.** Each command waits until the base reads it (≤ 15 s). If the
  converter is disconnected or the base does not read it, the action fails with an error —
  automations will stop at that step unless they use `continue_on_error`.
- **After a restart** the zone command registers are served as `0`, which the base treats as
  "no data" and answers by republishing its current values within seconds. Values shown in
  the meantime come from the last known state.
- **Changes on the thermostat or in the REHAU app** reach Home Assistant within ~10 s
  (zones). Global mode/state changes made in the app are usually *not* broadcast by the base.
- In global mode *Auto* the bus does not say whether the system is heating or cooling.

## Diagnostics and troubleshooting

- *Download diagnostics* on the integration shows connection state, counters, detected
  topology, pending commands and all registers.
- `tools/sysbus_capture.py` records raw SYSBUS traffic (standard library only) to look for
  undocumented registers; see [docs/protocol.md](docs/protocol.md).
- Debug logging:
  ```yaml
  logger:
    logs:
      custom_components.rehau_neasmart2: debug
  ```

## Credits

- [Matteo Manzoni](https://github.com/MatteoManzoni) for the original
  [gateway add-on](https://github.com/MatteoManzoni/RehauNeasmart2.0_Gateway) and
  [integration](https://github.com/MatteoManzoni/rehau-neasmart2.0-integration-ha), which
  first mapped this protocol; this project keeps its configuration format and unique IDs.
- The community findings collected in those repositories, in
  [tiagoroldao/rehau-nea-2-modbus-to-mqtt](https://github.com/tiagoroldao/rehau-nea-2-modbus-to-mqtt)
  and in the [Home Assistant forum thread](https://community.home-assistant.io/t/rehau-nea-smart-integration/433713).
- REHAU's *Gateway KNX NEA SMART 2.0* commissioning manual for the datapoint list.

Not affiliated with or endorsed by REHAU.

## License

MIT — see [LICENSE](LICENSE).
