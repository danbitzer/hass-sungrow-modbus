"""Errors raised by this library on top of modbus-connection's."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from modbus_connection import ModbusError

    from .report import WriteReport


class SungrowError(Exception):
    """Base class for every error this library raises itself."""


class UnsupportedModelError(SungrowError):
    """The device type code is not an SH-T inverter."""

    def __init__(self, code: int, known_name: str | None = None) -> None:
        self.code = code
        self.known_name = known_name
        if known_name is not None:
            message = f"{known_name} (0x{code:04X}) is not an SH-T inverter"
        else:
            message = f"device type code 0x{code:04X} is not a known SH-T inverter"
        super().__init__(message)


class ControlError(SungrowError):
    """Base class for errors from the guarded write layer."""


class PowerOutOfRangeError(ControlError, ValueError):
    """A requested power or limit is outside what the inverter accepts."""

    def __init__(self, what: str, value: Any, low: int, high: int) -> None:
        self.what, self.value, self.low, self.high = what, value, low, high
        super().__init__(f"{what} {value!r} is outside {low}..{high} W")


class SettingsUnavailableError(ControlError):
    """The settings a write depends on could not be read."""


class WriteRejectedError(ControlError):
    """The inverter refused a write; the writes before it stand."""

    def __init__(self, field: str, cause: ModbusError, report: WriteReport) -> None:
        self.field = field
        self.cause = cause
        self.report = report
        super().__init__(f"write of {field} rejected: {cause}")


class VerificationError(ControlError):
    """A register read back a value other than the one written."""

    def __init__(
        self, field: str, expected: Any, actual: Any, report: WriteReport
    ) -> None:
        self.field = field
        self.expected = expected
        self.actual = actual
        self.report = report
        super().__init__(f"{field} reads {actual!r} after writing {expected!r}")
