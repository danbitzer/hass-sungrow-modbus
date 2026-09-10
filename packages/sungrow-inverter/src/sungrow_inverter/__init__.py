"""Read and control Sungrow SH-T hybrid inverters over Modbus."""

from __future__ import annotations

from .const import HOLDING_RANGES, INPUT_RANGES
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
from .exceptions import ControlError, SungrowError, UnsupportedModelError
from .inverter import DEFAULT_FENCE_POWER_W, ProbeResult, SungrowInverter
from .models import OTHER_MODELS, SHT_MODELS, ShtModel, model_for
from .report import UpdateReport
from .retry import RetryingUnit, RetryPolicy

__version__ = "0.1.0a1"

__all__ = [
    "DEFAULT_FENCE_POWER_W",
    "DESIRED_BATTERY_MODES",
    "GENERATING_STATES",
    "HOLDING_RANGES",
    "INPUT_RANGES",
    "OTHER_MODELS",
    "RUNNING_STATES",
    "SHT_MODELS",
    "BatteryMode",
    "ChargeCommand",
    "ControlError",
    "EmsMode",
    "InverterState",
    "OutputType",
    "PowerFlow",
    "ProbeResult",
    "RetryPolicy",
    "RetryingUnit",
    "ShtModel",
    "StartStop",
    "SungrowError",
    "SungrowInverter",
    "UnsupportedModelError",
    "UpdateReport",
    "__version__",
    "model_for",
]
