# NEA SMART 2.0 SYSBUS protocol notes

What the integration relies on: REHAU's datapoint list for the *Gateway KNX NEA SMART 2.0*
(a Weinzierl KNX/Modbus 886) plus observations from a real installation (one base, six zones,
one mixed circuit, firmware current in late 2026) and from the community.

## Physical layer
RS485, **38400 baud, 8N1**, MSB first, register addresses start at 0.

## Roles
The **base is the Modbus master**. The KNX gateway (here: Home Assistant) is a slave and
must answer both unit IDs **240 and 241** — the base alternates between them.

| Direction | How | Period (observed) |
|---|---|---|
| Measurements, base → gateway | FC16 **broadcast (unit 0)** | each register every ~9–11 s |
| Commands, gateway → base | base polls with FC3 (unit 240/241): zone state+setpoint in one read, global mode and state separately | zones ~1.9 s, globals ~4.4 s |
| Changes made on a thermostat / app | base broadcasts the new zone state/setpoint | immediately |

The base **applies what it reads**. Serving an old value reverts a change made elsewhere;
serving **0** means "no data" and makes the base republish its own value.
Global mode/state (registers 1/2) are rarely or never republished.

## Register map

| Register | Content | Type |
|---|---|---|
| 1 | global mode: 1 auto, 2 heating, 3 cooling, 4 manual heating, 5 manual cooling | command |
| 2 | global state: 1 normal, 2 reduced, 3 standby, 4 time program, 5 party, 6 holiday/absence | command |
| 3, 4, 5 | error / warning / hint flags (0/1) — the manual lists 3/5/6, but the base writes 3, 4, 5 and never 6 | broadcast |
| 7, 8 | outside temperature, filtered outside temperature (DPT 9.001) | broadcast |
| 10–13, 14–17, 18–21 | mixed circuit 1/2/3: valve %, pump 0/1, flow, return (DPT 9.001) | broadcast |
| 22–30 | dehumidifier 1–9 running 0/1 | broadcast (only existing ones) |
| 31–35 | pump 1–5 running 0/1 | broadcast (only existing ones) |
| N + 0 | zone state (same values as global state) | command |
| N + 1 | zone setpoint (DPT 9.001); `0x8116` in standby or outside the season | command |
| N + 2 | zone temperature (DPT 9.001) | broadcast |
| N + 3 | **undocumented**, always 0 so far | broadcast |
| N + 10 | zone relative humidity % | broadcast |

`N = (base − 1) × 1200 + zone × 100`, base 1–5 (master + 4 slaves numbered by DIP switch),
zone 1–12. A thermostat set to *away* on its own menu reports state 2 (reduced).

DPT 9.001: 16 bits = sign (1) | exponent (4) | mantissa (11, two's complement with the sign),
value = 0.01 × mantissa × 2^exponent. `0x7FFF` = not initialised.

## Other traffic
- **Unit 128**: proprietary frames (~13/s with one base) that are not Modbus; a Waveshare
  converter forwards them with a non-zero MBAP protocol ID. Ignored.
- **Unit IDs 16, 24–44** (multi-base installations): REHAU's inter-controller protocol. Ignored.

## Not on the bus
Thermostat LED ring, child lock, window detection, per-room heat demand / valve state, dew
point, schedules, heating curve. Toggling the LED on a thermostat produces no SYSBUS traffic.
These are only reachable through REHAU's cloud API or the base's access-point web pages
(which disable the app and cloud while in use).

## Investigating
`tools/sysbus_capture.py` behaves like the gateway, logs every frame and prints a report of
written/read registers with the times they changed. Note the times of the actions you perform
on a thermostat and compare. Findings about register 4, N+3 or unit 128 are very welcome as issues.
