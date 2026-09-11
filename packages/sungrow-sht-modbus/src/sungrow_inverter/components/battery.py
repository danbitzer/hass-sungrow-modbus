"""Battery measurements."""

from __future__ import annotations

from modbus_connection.model import gauge, int32

from ..const import U16_NAN
from .base import SungrowInput


class Battery(SungrowInput):
    """Battery voltage, current, state of charge and health, temperature."""

    battery_current = gauge(5630, 0.1, unit="A")
    """Battery current (reg 5631); the spec prefers it over reg 13021."""
    battery_voltage = gauge(13019, 0.1, signed=False, nan=U16_NAN, unit="V")
    """Battery voltage (reg 13020)."""
    battery_level = gauge(13022, 0.1, signed=False, nan=U16_NAN, unit="%")
    """Battery state of charge (reg 13023)."""
    battery_soh = gauge(13023, 0.1, signed=False, nan=U16_NAN, unit="%")
    """Battery state of health (reg 13024)."""
    battery_temperature = gauge(13024, 0.1, unit="°C")
    """Battery temperature (reg 13025)."""


class BatteryPower(SungrowInput):
    """Battery power on its own, so a refused block cannot take the rest down."""

    battery_power = int32(5213, word_order="little", unit="W")
    """Battery power (reg 5214): positive = discharging, negative = charging.

    The sign matches mkaiser's ``sensor.battery_power``, so a consumer that
    treats discharge as positive needs no conversion.
    """
