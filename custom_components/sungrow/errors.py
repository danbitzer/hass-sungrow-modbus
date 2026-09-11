"""Map library and Modbus failures onto Home Assistant's typed errors."""

from __future__ import annotations

from typing import Any, NoReturn

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from modbus_connection import ModbusConnectionError, ModbusError, ModbusTimeoutError

from sungrow_inverter import (
    ControlError,
    InvalidWriteValueError,
    PowerOutOfRangeError,
    SettingsUnavailableError,
    VerificationError,
    WriteRejectedError,
    WriteUncertainError,
)

from .const import DOMAIN


def raise_for_write_error(field: str, value: Any, err: BaseException) -> NoReturn:
    """A single-register write (number/select/switch) failed."""
    match err:
        case ValueError():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_value",
                translation_placeholders={
                    "field": field,
                    "value": str(value),
                    "reason": str(err),
                },
            ) from err
        case ModbusTimeoutError() | ModbusConnectionError():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_uncertain",
                translation_placeholders={"field": field, "error": str(err)},
            ) from err
        case ModbusError():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"field": field, "error": str(err)},
            ) from err
        case _:
            raise err


def raise_for_control_error(err: BaseException) -> NoReturn:
    """A guarded control call (``BatteryControl``) failed."""
    match err:
        case PowerOutOfRangeError():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="power_out_of_range",
                translation_placeholders={
                    "what": err.what,
                    "value": str(err.value),
                    "low": str(err.low),
                    "high": str(err.high),
                },
            ) from err
        case InvalidWriteValueError():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_value",
                translation_placeholders={
                    "field": err.field,
                    "value": str(err.value),
                    "reason": err.reason,
                },
            ) from err
        case VerificationError():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="verification_failed",
                translation_placeholders={
                    "field": err.field,
                    "expected": str(getattr(err.expected, "value", err.expected)),
                    "actual": str(getattr(err.actual, "value", err.actual)),
                },
            ) from err
        case WriteUncertainError():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_uncertain",
                translation_placeholders={"field": err.field, "error": str(err.cause)},
            ) from err
        case WriteRejectedError():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="write_failed",
                translation_placeholders={"field": err.field, "error": str(err.cause)},
            ) from err
        case SettingsUnavailableError() | ControlError() | ModbusError():
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="control_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        case ValueError():
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="control_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        case _:
            raise err
