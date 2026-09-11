"""Register map constants: readable ranges, sentinels and command words.

Addresses are Modbus protocol addresses, one below the register number in
Sungrow's document (``reg 13000`` is address 12999).
"""

from __future__ import annotations

from modbus_connection.model import Range

MAX_SPAN = 100
"""Widest block read this library asks for.

A WiNet-S (firmware V300) answered a 125-register input read in one frame;
the widest block any component needs is the 83-register settings block.
"""

INPUT_RANGES: tuple[Range, ...] = (
    (4951, 5034),  # identity, ratings, output energy, temperature, MPPT, AC
    (5213, 5241),  # battery power ... grid frequency
    (5600, 5638),  # meter powers, feed-in limits, BDC, battery current, BMS, capacity
    (5722, 5745),  # backup power, meter voltages and currents
    (12999, 13078),  # running state, power flow, energies, battery, alarms
    (13249, 13293),  # firmware strings
)
"""Input-register (FC04) addresses an SH-T behind a WiNet-S answers.

Surveyed on an SH15T (firmware P063, WiNet-S V300): every span above reads
in one frame, including the documented reserved holes inside it (5005-5009,
5021-5031, 13029, 13043, 13047-13048), which answer 0. A read that *starts*
on a reserved address is refused with exception 2, so a range must start on
a documented register — the planner starts every block on a field.
"""

HOLDING_RANGES: tuple[Range, ...] = (
    (13017, 13099),  # PV limitation ... reserved SoC for backup (Table 4)
    (31212, 31212),  # APL shutdown-at-zero shadow (undocumented)
    (33046, 33149),  # max charge/discharge power ... start power
)
"""Holding-register (FC03) addresses this library reads.

Surveyed as above: 13017-13099 (83 registers) and 33046-33149 answer in one
frame each; the reserved registers inside read 0xFFFF or 0. Holding reads
are slow on a WiNet-S (0.1-0.3 s each) and answer exception 4 now and then
while another client polls — that is contention, retried by
``RetryingUnit``, not a refusal.
"""

AA = 0xAA
"""Enable / limit / charge word."""
X55 = 0x55
"""Disable / allow word."""

U16_NAN = 0xFFFF
S16_NAN = 0x7FFF
S32_NAN = 0x7FFF_FFFF
