"""Base classes carrying the readable ranges for each register space."""

from __future__ import annotations

from modbus_connection.model import Component

from ..const import HOLDING_RANGES, INPUT_RANGES, MAX_SPAN


class SungrowInput(Component):
    """A sub-system read from the input registers (FC04)."""

    register_space = "input"
    register_ranges = INPUT_RANGES
    max_span = MAX_SPAN


class SungrowHolding(Component):
    """A sub-system read from, and written to, the holding registers (FC03/06)."""

    register_space = "holding"
    register_ranges = HOLDING_RANGES
    max_span = MAX_SPAN
