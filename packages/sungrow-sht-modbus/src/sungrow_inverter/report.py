"""What one poll managed to refresh, and what one control call wrote."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

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


@dataclass(frozen=True)
class WriteRecord:
    """One register write the control layer made."""

    component: str
    field: str
    previous: Any
    value: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "field": self.field,
            "previous": _plain(self.previous),
            "value": _plain(self.value),
        }


@dataclass
class WriteReport:
    """What one control call did: the writes, the writes it did not need,
    and whether the read-back matched."""

    action: str
    writes: list[WriteRecord] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    """``component.field`` targets already at their value."""
    uncertain: list[str] = field(default_factory=list)
    """Targets whose write got no answer: the value may have landed."""
    verified: bool = False
    at: float = field(default_factory=time.monotonic)
    """``time.monotonic()`` when the call started."""

    def as_dict(self) -> dict[str, Any]:
        """A JSON-safe rendering, the shape a service response returns."""
        return {
            "action": self.action,
            "writes": [w.as_dict() for w in self.writes],
            "skipped": list(self.skipped),
            "uncertain": list(self.uncertain),
            "verified": self.verified,
        }


def _plain(value: Any) -> Any:
    """Enums by their value, so the rendering needs no library types."""
    return getattr(value, "value", value)
