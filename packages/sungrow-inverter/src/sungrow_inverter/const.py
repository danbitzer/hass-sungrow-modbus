"""Register map constants: readable ranges, sentinels and command words.

Addresses are Modbus protocol addresses, one below the register number in
Sungrow's document (``reg 13000`` is address 12999).
"""

from __future__ import annotations

from modbus_connection.model import Range

MAX_SPAN = 64
"""Widest block read this library asks for. A WiNet-S is safer below 125."""

INPUT_RANGES: tuple[Range, ...] = (
    (4951, 4982),  # protocol version, ARM + DSP certification strings
    (4989, 5004),  # serial, device type, nominal power, output type, output energy
    (5007, 5007),  # inside temperature
    (5010, 5020),  # MPPT 1-3, total DC power, phase voltages
    (5032, 5034),  # reactive power, power factor
    (5213, 5214),  # battery power (S32)
    (5241, 5241),  # grid frequency (0.01 Hz)
    (5600, 5607),  # meter active power, total + per phase
    (5621, 5622),  # feed-in limit min/max
    (5627, 5627),  # BDC rated power
    (5630, 5630),  # battery current
    (5634, 5635),  # BMS max charge/discharge current
    (5638, 5638),  # battery capacity, high precision
    (5722, 5726),  # backup power per phase + total
    (5740, 5745),  # meter voltages and currents
    (12999, 13028),  # running state, power flow, energies, load/export, battery
    (13030, 13042),  # phase currents, total active power, import, charge, DRM
    (13044, 13046),  # export energy
    (13049, 13078),  # alarm and fault words
    (13249, 13293),  # firmware strings
)
"""Input-register (FC04) addresses an SH-T answers, split at documented holes.

Conservative first cut; a live run may prove the WiNet-S serves adjacent
ranges in one block, in which case they are merged.
"""

HOLDING_RANGES: tuple[Range, ...] = (
    (13017, 13017),  # PV power limitation
    (13049, 13051),  # EMS mode, charge command, forced power
    (13057, 13058),  # max / min SoC
    (13073, 13074),  # feed-in limit value, off-grid option
    (13086, 13089),  # feed-in limit enable/ratio, active power limit enable/ratio
    (13099, 13099),  # reserved SoC for backup
    (31212, 31212),  # APL shutdown-at-zero shadow (undocumented)
    (33046, 33047),  # max charge / discharge power
    (33148, 33149),  # charging / discharging start power
)
"""Holding-register (FC03) addresses this library reads."""

AA = 0xAA
"""Enable / limit / charge word."""
X55 = 0x55
"""Disable / allow word."""

U16_NAN = 0xFFFF
S16_NAN = 0x7FFF
U32_NAN = 0xFFFF_FFFF
S32_NAN = 0x7FFF_FFFF
