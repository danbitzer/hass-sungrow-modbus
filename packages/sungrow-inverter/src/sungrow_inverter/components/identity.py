"""What never changes: model, serial, ratings and limits. Read once."""

from __future__ import annotations

from modbus_connection.model import enum, gauge, integer, string, uint32

from ..const import U16_NAN
from ..enums import OutputType
from .base import SungrowInput


class Identity(SungrowInput):
    """Identity and rating registers (Table 3), read at setup and never polled."""

    protocol_version = uint32(4951, word_order="little")
    """Protocol version (reg 4952), e.g. 0x01010700 for V1.1.7."""
    arm_version = string(4953, 15)
    """Certification version of the ARM software (reg 4954)."""
    dsp_version = string(4968, 15)
    """Certification version of the DSP software (reg 4969)."""
    serial = string(4989, 10)
    """Serial number (reg 4990)."""
    device_type_code = integer(4999, signed=False)
    """Device type code (reg 5000), see Appendix 1."""
    nominal_power = gauge(5000, 100, signed=False, unit="W")
    """Nominal output power (reg 5001), 0.1 kW per count."""
    output_type = enum(5001, OutputType)
    """Output type (reg 5002)."""
    export_limit_min = gauge(5621, 10, signed=False, nan=U16_NAN, unit="W")
    """Min. feed-in power limitation value (reg 5622), 0.01 kW per count."""
    export_limit_max = gauge(5622, 10, signed=False, nan=U16_NAN, unit="W")
    """Max. feed-in power limitation value (reg 5623), 0.01 kW per count."""
    bdc_rated_power = gauge(5627, 100, signed=False, nan=U16_NAN, unit="W")
    """BDC (battery DC converter) rated power (reg 5628), 0.1 kW per count."""
    bms_max_charge_current = integer(5634, signed=False, nan=U16_NAN, unit="A")
    """Max. charging current reported by the BMS (reg 5635)."""
    bms_max_discharge_current = integer(5635, signed=False, nan=U16_NAN, unit="A")
    """Max. discharging current reported by the BMS (reg 5636)."""
    battery_capacity = gauge(5638, 0.01, signed=False, nan=U16_NAN, unit="kWh")
    """Battery capacity, high precision (reg 5639), 0.01 kWh per count."""

    @property
    def protocol_version_text(self) -> str | None:
        """The protocol version as ``V1.1.7``."""
        raw = self.protocol_version
        if raw is None:
            return None
        return f"V{raw >> 24 & 0xFF}.{raw >> 16 & 0xFF}.{raw >> 8 & 0xFF}"
