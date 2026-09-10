"""The start/stop command register. Write-only; never polled."""

from __future__ import annotations

from modbus_connection.model import Component, enum

from ..const import MAX_SPAN
from ..enums import StartStop


class Control(Component):
    """Start/Stop (reg 13000, holding).

    Reading it back is not meaningful, so this component is never updated;
    it exists to give the command a typed ``write``.
    """

    register_space = "holding"
    register_ranges = ((12999, 12999),)
    max_span = MAX_SPAN

    start_stop = enum(12999, StartStop, writable=True)
    """Start/Stop command (reg 13000): 0xCF boot, 0xCE shutdown."""
