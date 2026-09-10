"""What one poll managed to refresh."""

from __future__ import annotations

from dataclasses import dataclass, field

from modbus_connection import ModbusError


@dataclass
class UpdateReport:
    """The components a poll refreshed, and the ones that failed with why."""

    updated: list[str] = field(default_factory=list)
    failed: dict[str, ModbusError] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Whether every component polled refreshed."""
        return not self.failed
