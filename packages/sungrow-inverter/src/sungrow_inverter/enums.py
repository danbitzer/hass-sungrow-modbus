"""Enumerations for the values an SH-T inverter reports and accepts."""

from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum, IntFlag, StrEnum


class InverterState(StrEnum):
    """Running state (reg 13000), per Appendix 2 of the Sungrow protocol.

    Several states are reported under two codes (a 16-bit word on newer
    firmware, a bit on older); ``RUNNING_STATES`` maps both to one member.
    """

    RUNNING = "running"
    MICROGRID_OPERATION = "microgrid_operation"
    MAINTAIN_MODE = "maintain_mode"
    COMPULSORY_MODE = "compulsory_mode"
    OFF_GRID = "off_grid"
    OPEN_LOOP = "open_loop"
    EXTERNAL_EMS = "external_ems"
    EMERGENCY_CHARGING = "emergency_charging"
    UNINITIALIZED = "uninitialized"
    STOP = "stop"
    KEY_STOP = "key_stop"
    EMERGENCY_STOP = "emergency_stop"
    STANDBY = "standby"
    INITIAL_STANDBY = "initial_standby"
    STARTING = "starting"
    STATION_BUILDING = "station_building"
    WARN_RUNNING = "warn_running"
    DERATING_RUNNING = "derating_running"
    DISPATCH_RUNNING = "dispatch_running"
    FAULT = "fault"
    AFCI_SELF_TEST_SHUTDOWN = "afci_self_test_shutdown"
    SAFE_MODE = "safe_mode"
    OFF_GRID_CHARGE = "off_grid_charge"
    UPDATE_FAILED = "update_failed"
    COMMUNICATE_FAULT = "communicate_fault"
    RESTARTING = "restarting"


RUNNING_STATES: Mapping[int, InverterState] = {
    0x0000: InverterState.RUNNING,
    0x0040: InverterState.RUNNING,
    0x0014: InverterState.MICROGRID_OPERATION,
    0x0400: InverterState.MAINTAIN_MODE,
    0x0800: InverterState.COMPULSORY_MODE,
    0x1000: InverterState.OFF_GRID,
    0x2000: InverterState.OPEN_LOOP,
    0x4000: InverterState.EXTERNAL_EMS,
    0x4001: InverterState.EMERGENCY_CHARGING,
    0x1111: InverterState.UNINITIALIZED,
    0x8000: InverterState.STOP,
    0x0001: InverterState.STOP,
    0x1300: InverterState.KEY_STOP,
    0x0002: InverterState.KEY_STOP,
    0x1500: InverterState.EMERGENCY_STOP,
    0x0004: InverterState.EMERGENCY_STOP,
    0x1400: InverterState.STANDBY,
    0x0008: InverterState.STANDBY,
    0x1200: InverterState.INITIAL_STANDBY,
    0x0010: InverterState.INITIAL_STANDBY,
    0x1600: InverterState.STARTING,
    0x0020: InverterState.STARTING,
    0x1800: InverterState.STATION_BUILDING,
    0x9100: InverterState.WARN_RUNNING,
    0x8100: InverterState.DERATING_RUNNING,
    0x0080: InverterState.DERATING_RUNNING,
    0x8200: InverterState.DISPATCH_RUNNING,
    0x5500: InverterState.FAULT,
    0x0100: InverterState.FAULT,
    0x1700: InverterState.AFCI_SELF_TEST_SHUTDOWN,
    0x1900: InverterState.SAFE_MODE,
    0x0041: InverterState.OFF_GRID_CHARGE,
    0x0200: InverterState.UPDATE_FAILED,
    0x2500: InverterState.COMMUNICATE_FAULT,
    0x2501: InverterState.RESTARTING,
}
"""Running-state word (reg 13000) to ``InverterState``."""

GENERATING_STATES: frozenset[InverterState] = frozenset(
    {
        InverterState.RUNNING,
        InverterState.MICROGRID_OPERATION,
        InverterState.MAINTAIN_MODE,
        InverterState.COMPULSORY_MODE,
        InverterState.OFF_GRID,
        InverterState.EXTERNAL_EMS,
        InverterState.EMERGENCY_CHARGING,
        InverterState.WARN_RUNNING,
        InverterState.DERATING_RUNNING,
        InverterState.DISPATCH_RUNNING,
    }
)
"""States in which the spec marks the inverter as able to generate power."""


class PowerFlow(IntFlag):
    """Power flow status bits (reg 13001), per Appendix 3."""

    PV_GENERATING = 1
    BATTERY_CHARGING = 2
    BATTERY_DISCHARGING = 4
    LOAD_POSITIVE = 8
    EXPORTING = 16
    IMPORTING = 32
    LOAD_NEGATIVE = 128


class EmsMode(IntEnum):
    """EMS mode selection (reg 13050)."""

    SELF_CONSUMPTION = 0
    COMPULSORY = 2
    EXTERNAL_EMS = 3
    VPP = 4


class ChargeCommand(IntEnum):
    """Charge/discharge command (reg 13051), effective in compulsory mode."""

    CHARGE = 0xAA
    DISCHARGE = 0xBB
    STOP = 0xCC


class StartStop(IntEnum):
    """Start/Stop command (reg 13000, holding)."""

    START = 0xCF
    STOP = 0xCE


class OutputType(IntEnum):
    """Output type (reg 5002)."""

    SINGLE_PHASE = 0
    THREE_PHASE_4_WIRE = 1
    THREE_PHASE_3_WIRE = 2


class BatteryMode(StrEnum):
    """The battery behaviour a caller asks for, or the one the settings imply.

    The first five can be requested; the rest are only ever reported by
    ``effective_mode`` for settings this library does not write itself.
    """

    SELF_CONSUMPTION = "self_consumption"
    FORCED_CHARGE = "forced_charge"
    FORCED_DISCHARGE = "forced_discharge"
    NO_CHARGE = "no_charge"
    HOLD = "hold"
    # effective-only
    FORCED_STOP = "forced_stop"
    NO_DISCHARGE = "no_discharge"
    EXTERNAL_EMS = "external_ems"
    VPP = "vpp"
    UNKNOWN = "unknown"


DESIRED_BATTERY_MODES: frozenset[BatteryMode] = frozenset(
    {
        BatteryMode.SELF_CONSUMPTION,
        BatteryMode.FORCED_CHARGE,
        BatteryMode.FORCED_DISCHARGE,
        BatteryMode.NO_CHARGE,
        BatteryMode.HOLD,
    }
)
"""The modes ``BatteryControl.apply`` accepts."""
