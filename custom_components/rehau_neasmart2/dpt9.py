"""KNX DPT 9.001 (2-byte float) used by REHAU for temperatures."""

from __future__ import annotations

# 0x7FFF = not initialised; 0x8116 = setpoint not applicable (zone in standby or
# outside the heating/cooling season). Neither is a real temperature.
INVALID = frozenset({0x7FFF, 0x8116})
MIN_REASONABLE = -50.0
MAX_REASONABLE = 120.0


def pack(value: float) -> int:
    """Encode a float as DPT 9.001."""
    mantissa = int(round(value * 100))
    exponent = 0
    while mantissa > 2047 or mantissa < -2048:
        mantissa //= 2
        exponent += 1
    if exponent > 15:
        raise ValueError(f"value out of DPT 9 range: {value}")
    sign = 0
    if mantissa < 0:
        sign = 1
        mantissa += 2048
    return (sign << 15) | (exponent << 11) | (mantissa & 0x7FF)


def unpack(raw: int) -> float:
    """Decode a DPT 9.001 register (full sign and exponent handling)."""
    mantissa = raw & 0x7FF
    if raw & 0x8000:
        mantissa -= 2048
    exponent = (raw >> 11) & 0xF
    return round(0.01 * mantissa * (1 << exponent), 2)


def temperature(raw: int | None) -> float | None:
    """Return a valid temperature, or None for sentinels and implausible values."""
    if raw is None or raw in INVALID:
        return None
    value = unpack(raw)
    if not MIN_REASONABLE <= value <= MAX_REASONABLE:
        return None
    return value
