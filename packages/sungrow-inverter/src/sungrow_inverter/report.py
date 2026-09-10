"""What one poll managed to refresh."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from modbus_connection import ModbusError

Raw = dict[str, dict[int, int | bool]]
"""Raw register words keyed ``{space: {address: value}}``."""


@dataclass
class UpdateReport:
    """The components a poll refreshed, and the ones that failed with why."""

    updated: list[str] = field(default_factory=list)
    failed: dict[str, ModbusError] = field(default_factory=dict)
    at: float = field(default_factory=time.monotonic)
    """``time.monotonic()`` when the poll started."""
    raw: Raw | None = None
    """The raw words the poll read, when it was asked to collect them."""

    @property
    def ok(self) -> bool:
        """Whether every component polled refreshed."""
        return not self.failed
