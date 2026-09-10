"""Firmware version strings. SH-T serves them; some paths return empty strings."""

from __future__ import annotations

from modbus_connection.model import string

from .base import SungrowInput


class FirmwareInfo(SungrowInput):
    """Firmware information strings (Table 3, regs 13250-13294). Read once."""

    inverter_firmware = string(13249, 15)
    """Inverter firmware information (reg 13250)."""
    comm_module_firmware = string(13264, 15)
    """Communication module (WiNet) firmware information (reg 13265)."""
    battery_firmware = string(13279, 15)
    """Battery firmware information (reg 13280)."""

    @property
    def empty(self) -> bool:
        """Whether every string read back blank (a WiNet-S refusing the block)."""
        return not any(
            (self.inverter_firmware, self.comm_module_firmware, self.battery_firmware)
        )
