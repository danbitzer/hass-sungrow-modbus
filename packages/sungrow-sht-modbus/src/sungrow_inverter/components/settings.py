"""Settings (Table 4): what the inverter has been told to do."""

from __future__ import annotations

from modbus_connection.model import enum, gauge, integer

from ..const import U16_NAN
from ..enums import ChargeCommand, EmsMode
from ..fields import (
    aa55,
    multiple_of_10,
    non_negative_u16,
    percent,
    ratio,
    soc_lower,
    soc_upper,
)
from .base import SungrowHolding


class Settings(SungrowHolding):
    """EMS, battery and limit settings.

    Polled slowly: these are read-write registers and a WiNet-S must not be
    hammered on them.
    """

    pv_power_limitation = aa55(13017)
    """PV power limitation (reg 13018): True = 0xAA limit PV, False = 0x55 allow
    PV. SH-T only."""
    ems_mode = enum(13049, EmsMode, writable=True)
    """EMS mode selection (reg 13050)."""
    charge_command = enum(13050, ChargeCommand, writable=True)
    """Charge/discharge command (reg 13051): 0xAA charge, 0xBB discharge,
    0xCC stop."""
    forced_power = integer(13051, signed=False, unit="W", writable=non_negative_u16)
    """Charge/discharge power in compulsory mode (reg 13052), W."""
    max_soc = gauge(13057, 0.1, signed=False, unit="%", writable=soc_upper)
    """Max. SoC (reg 13058), 50.0-100.0 %."""
    min_soc = gauge(13058, 0.1, signed=False, unit="%", writable=soc_lower)
    """Min. SoC (reg 13059), 0.0-50.0 %."""
    export_limit = integer(13073, signed=False, unit="W", writable=non_negative_u16)
    """Feed-in limitation value (reg 13074), W; 1 W per count on SH-T."""
    backup_mode = aa55(13074)
    """Off-grid (backup) option (reg 13075)."""
    export_limit_enabled = aa55(13086)
    """Feed-in limitation enable (reg 13087)."""
    feed_in_ratio = gauge(
        13087, 0.1, signed=False, nan=U16_NAN, unit="%", writable=ratio
    )
    """Feed-in limitation ratio (reg 13088), 0.1 %; takes precedence over the
    W value when both are set."""
    active_power_limit_enabled = aa55(13088)
    """Active power limitation enable (reg 13089)."""
    active_power_limit_ratio = gauge(
        13089, 0.1, signed=False, nan=U16_NAN, unit="%", writable=ratio
    )
    """Active power limit ratio (reg 13090), 0.1 %."""
    backup_reserve_soc = integer(13099, signed=False, unit="%", writable=percent)
    """Reserved SoC for backup (reg 13100), %."""


class BatteryLimits(SungrowHolding):
    """The battery power fences the guarded write layer uses."""

    max_charge_power = gauge(
        33046, 10, signed=False, nan=U16_NAN, unit="W", writable=multiple_of_10
    )
    """Max. charging power (reg 33047), 0.01 kW per count."""
    max_discharge_power = gauge(
        33047, 10, signed=False, nan=U16_NAN, unit="W", writable=multiple_of_10
    )
    """Max. discharging power (reg 33048), 0.01 kW per count."""


class StartPower(SungrowHolding):
    """Charge/discharge start thresholds. Optional: not every firmware serves them."""

    charging_start_power = gauge(33148, 10, signed=False, nan=U16_NAN, unit="W")
    """Charging start power (reg 33149), 0.01 kW per count."""
    discharging_start_power = gauge(33149, 10, signed=False, nan=U16_NAN, unit="W")
    """Discharging start power (reg 33150), 0.01 kW per count."""


class AplShadow(SungrowHolding):
    """iSolarCloud's shadow of the active-power-limit behaviour. Read-only."""

    apl_shutdown_at_zero = aa55(31212, writable=False)
    """Shut down at an active power limit of 0 % (reg 31213, undocumented).

    When True, an active power limit ratio of 0 stops the inverter instead
    of merely curtailing it.
    """
