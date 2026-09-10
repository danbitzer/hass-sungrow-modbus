"""Errors raised by this library on top of modbus-connection's."""

from __future__ import annotations


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
