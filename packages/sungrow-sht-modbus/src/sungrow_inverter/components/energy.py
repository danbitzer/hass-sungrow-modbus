"""Energy counters and the slowly-moving temperature. Polled every minute."""

from __future__ import annotations

from modbus_connection.model import gauge, uint32

from ..const import U16_NAN
from .base import SungrowInput


class Energy(SungrowInput):
    """Daily and lifetime energy counters (0.1 kWh), plus inside temperature."""

    daily_output_energy = gauge(5002, 0.1, signed=False, nan=U16_NAN, unit="kWh")
    """Daily output energy, PV generation plus battery discharge (reg 5003)."""
    total_output_energy = uint32(5003, scale=0.1, word_order="little", unit="kWh")
    """Total output energy (reg 5004)."""
    inverter_temperature = gauge(5007, 0.1, unit="°C")
    """Inside temperature (reg 5008)."""
    daily_pv_generation = gauge(13001, 0.1, signed=False, nan=U16_NAN, unit="kWh")
    """Daily PV generation (reg 13002)."""
    total_pv_generation = uint32(13002, scale=0.1, word_order="little", unit="kWh")
    """Total PV generation (reg 13003)."""
    daily_export_from_pv = gauge(13004, 0.1, signed=False, nan=U16_NAN, unit="kWh")
    """Daily export energy from PV (reg 13005)."""
    total_export_from_pv = uint32(13005, scale=0.1, word_order="little", unit="kWh")
    """Total export energy from PV (reg 13006)."""
    daily_battery_charge_from_pv = gauge(
        13011, 0.1, signed=False, nan=U16_NAN, unit="kWh"
    )
    """Daily battery charge energy from PV (reg 13012)."""
    total_battery_charge_from_pv = uint32(
        13012, scale=0.1, word_order="little", unit="kWh"
    )
    """Total battery charge energy from PV (reg 13013)."""
    daily_direct_consumption = gauge(13016, 0.1, signed=False, nan=U16_NAN, unit="kWh")
    """Daily direct energy consumption from PV (reg 13017)."""
    total_direct_consumption = uint32(13017, scale=0.1, word_order="little", unit="kWh")
    """Total direct energy consumption from PV (reg 13018)."""
    daily_battery_discharge = gauge(13025, 0.1, signed=False, nan=U16_NAN, unit="kWh")
    """Daily battery discharge energy (reg 13026)."""
    total_battery_discharge = uint32(13026, scale=0.1, word_order="little", unit="kWh")
    """Total battery discharge energy (reg 13027)."""
    self_consumption_today = gauge(13028, 0.1, signed=False, nan=U16_NAN, unit="%")
    """Self-consumption of today (reg 13029)."""
    daily_import = gauge(13035, 0.1, signed=False, nan=U16_NAN, unit="kWh")
    """Daily import energy (reg 13036)."""
    total_import = uint32(13036, scale=0.1, word_order="little", unit="kWh")
    """Total import energy (reg 13037)."""
    daily_battery_charge = gauge(13039, 0.1, signed=False, nan=U16_NAN, unit="kWh")
    """Daily battery charge energy (reg 13040)."""
    total_battery_charge = uint32(13040, scale=0.1, word_order="little", unit="kWh")
    """Total battery charge energy (reg 13041)."""
    daily_export = gauge(13044, 0.1, signed=False, nan=U16_NAN, unit="kWh")
    """Daily export energy (reg 13045)."""
    total_export = uint32(13045, scale=0.1, word_order="little", unit="kWh")
    """Total export energy (reg 13046)."""
