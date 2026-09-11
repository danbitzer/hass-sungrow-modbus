"""Read and control Sungrow SH-T hybrid inverters over Modbus."""

from __future__ import annotations

from .const import HOLDING_RANGES, INPUT_RANGES
from .control import BatteryControl, DesiredState, PlannedWrite
from .enums import (
    DESIRED_BATTERY_MODES,
    GENERATING_STATES,
    RUNNING_STATES,
    BatteryMode,
    ChargeCommand,
    EmsMode,
    InverterState,
    OutputType,
    PowerFlow,
    StartStop,
)
from .exceptions import (
    ControlError,
    InvalidWriteValueError,
    PowerOutOfRangeError,
    SettingsUnavailableError,
    SungrowError,
    UnsupportedModelError,
    VerificationError,
    WriteRejectedError,
    WriteUncertainError,
)
from .inverter import DEFAULT_FENCE_POWER_W, ProbeResult, SungrowInverter
from .models import OTHER_MODELS, SHT_MODELS, ShtModel, model_for
from .report import UpdateReport, WriteRecord, WriteReport
from .retry import RetryingUnit, RetryPolicy

__version__ = "0.1.0a2"

__all__ = [
    "DEFAULT_FENCE_POWER_W",
    "DESIRED_BATTERY_MODES",
    "GENERATING_STATES",
    "HOLDING_RANGES",
    "INPUT_RANGES",
    "OTHER_MODELS",
    "RUNNING_STATES",
    "SHT_MODELS",
    "BatteryControl",
    "BatteryMode",
    "ChargeCommand",
    "ControlError",
    "DesiredState",
    "EmsMode",
    "InvalidWriteValueError",
    "InverterState",
    "OutputType",
    "PlannedWrite",
    "PowerFlow",
    "PowerOutOfRangeError",
    "ProbeResult",
    "RetryPolicy",
    "RetryingUnit",
    "SettingsUnavailableError",
    "ShtModel",
    "StartStop",
    "SungrowError",
    "SungrowInverter",
    "UnsupportedModelError",
    "UpdateReport",
    "VerificationError",
    "WriteRecord",
    "WriteRejectedError",
    "WriteReport",
    "WriteUncertainError",
    "__version__",
    "model_for",
]
