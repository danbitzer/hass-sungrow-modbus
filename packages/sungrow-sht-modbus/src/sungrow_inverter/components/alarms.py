"""Alarm and fault words (Table 3, regs 13050-13079). Raw 32-bit masks."""

from __future__ import annotations

from modbus_connection.model import uint32

from .base import SungrowInput


class Alarms(SungrowInput):
    """Fifteen 32-bit alarm/fault words; see Appendix 4 for the bit meanings."""

    inverter_alarm = uint32(13049, word_order="little")
    """Inverter alarm (reg 13050)."""
    grid_fault = uint32(13051, word_order="little")
    """Grid-side fault (reg 13052)."""
    system_fault_1 = uint32(13053, word_order="little")
    """System fault 1 (reg 13054)."""
    system_fault_2 = uint32(13055, word_order="little")
    """System fault 2 (reg 13056)."""
    dc_fault = uint32(13057, word_order="little")
    """DC-side fault (reg 13058)."""
    permanent_fault = uint32(13059, word_order="little")
    """Permanent fault (reg 13060)."""
    bdc_fault = uint32(13061, word_order="little")
    """BDC-side fault (reg 13062)."""
    bdc_permanent_fault = uint32(13063, word_order="little")
    """BDC-side permanent fault (reg 13064)."""
    battery_fault = uint32(13065, word_order="little")
    """Battery fault (reg 13066)."""
    battery_alarm = uint32(13067, word_order="little")
    """Battery alarm (reg 13068)."""
    bms_alarm = uint32(13069, word_order="little")
    """BMS alarm (reg 13070)."""
    bms_protection = uint32(13071, word_order="little")
    """BMS protection (reg 13072)."""
    bms_fault_1 = uint32(13073, word_order="little")
    """BMS fault 1 (reg 13074)."""
    bms_fault_2 = uint32(13075, word_order="little")
    """BMS fault 2 (reg 13076)."""
    bms_alarm_2 = uint32(13077, word_order="little")
    """BMS alarm 2 (reg 13078)."""

    @property
    def any_active(self) -> bool | None:
        """Whether any alarm or fault word is non-zero; None before a read."""
        values = [getattr(self, name) for name in self.declared_fields]
        if all(value is None for value in values):
            return None
        return any(values)
