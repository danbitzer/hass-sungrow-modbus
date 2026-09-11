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
    (5722, 5745),  # backup power, voltages and frequency
    (12999, 13078),  # running state, power flow, energies, battery, alarms
    (13249, 13293),  # firmware strings
)
"""Input-register (FC04) addresses an SH-T behind a WiNet-S answers.

Surveyed on an SH15T (firmware P063, WiNet-S V300): every span above reads
in one frame, including the documented reserved holes inside it (5005-5006,
5021-5031, 13029, 13042-13043 were captured and answer 0). A read that
*starts* on a reserved or unforwarded address is refused with exception 2
(5006, 5008, 13043, 13047, 13014, and mkaiser's undocumented meter phase
registers at 5740, which read 0 inside a wider block), so a block must start
on a register the dongle forwards — the planner starts every block on a
field.

Other firmware or dongles may refuse a read that merely spans a hole; the
device object then re-plans that component against ``NARROW_INPUT_RANGES``,
the M1 map split at every documented hole.
"""

HOLDING_RANGES: tuple[Range, ...] = (
    (13017, 13099),  # PV limitation ... reserved SoC for backup (Table 4)
    (31212, 31212),  # APL shutdown-at-zero shadow (undocumented)
    (33046, 33149),  # max charge/discharge power ... start power
)
"""Holding-register (FC03) addresses this library reads.

Surveyed as above: 13017-13099 (83 registers) answers in one frame, as does
33046-33149 (104, wider than ``MAX_SPAN``, so the planner still issues two
reads there). The reserved registers inside read 0xFFFF, except 13052 and
13079 which read 0 — so a 0 is not proof of an unserved register. Holding
reads are slow on a WiNet-S (0.1-0.5 s, the 83-register block 0.3-0.5 s)
and answer exception 4 now and then while another client polls — that is
contention, retried by ``RetryingUnit``, not a refusal; the wide block
failed less often than the narrow reads it replaced.
"""

NARROW_INPUT_RANGES: tuple[Range, ...] = (
    (4951, 4982),
    (4989, 5004),
    (5007, 5007),
    (5010, 5020),
    (5032, 5034),
    (5213, 5214),
    (5241, 5241),
    (5600, 5607),
    (5621, 5622),
    (5627, 5627),
    (5630, 5630),
    (5634, 5635),
    (5638, 5638),
    (5722, 5726),
    (5740, 5745),
    (12999, 13028),
    (13030, 13042),
    (13044, 13046),
    (13049, 13078),
    (13249, 13293),
)
"""The input map split at every documented reserved hole: the fallback for a
device that refuses a read spanning one."""

NARROW_HOLDING_RANGES: tuple[Range, ...] = (
    (13017, 13017),
    (13049, 13051),
    (13057, 13058),
    (13073, 13074),
    (13086, 13089),
    (13099, 13099),
    (31212, 31212),
    (33046, 33047),
    (33148, 33149),
)
"""The holding map split at every documented reserved hole (fallback)."""

AA = 0xAA
"""Enable / limit / charge word."""
X55 = 0x55
"""Disable / allow word."""

U16_NAN = 0xFFFF
S16_NAN = 0x7FFF
S32_NAN = 0x7FFF_FFFF
