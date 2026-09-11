"""Fast-changing measurements, polled every few seconds."""

from __future__ import annotations

from modbus_connection.model import NumberField, flags, gauge, int32, integer, uint32

from ..const import S16_NAN, S32_NAN, U16_NAN
from ..enums import RUNNING_STATES, InverterState, PowerFlow
from .base import SungrowInput


def _power(voltage: float | None, current: float | None) -> float | None:
    if voltage is None or current is None:
        return None
    return round(voltage * current, 1)


class AcDc(SungrowInput):
    """PV strings, DC power and the grid-side AC quantities."""

    mppt1_voltage = gauge(5010, 0.1, signed=False, nan=U16_NAN, unit="V")
    """MPPT 1 voltage (reg 5011)."""
    mppt1_current = gauge(5011, 0.1, signed=False, nan=U16_NAN, unit="A")
    """MPPT 1 current (reg 5012)."""
    mppt2_voltage = gauge(5012, 0.1, signed=False, nan=U16_NAN, unit="V")
    """MPPT 2 voltage (reg 5013)."""
    mppt2_current = gauge(5013, 0.1, signed=False, nan=U16_NAN, unit="A")
    """MPPT 2 current (reg 5014)."""
    mppt3_voltage = gauge(5014, 0.1, signed=False, nan=U16_NAN, unit="V")
    """MPPT 3 voltage (reg 5015); excluded on two-MPPT models."""
    mppt3_current = gauge(5015, 0.1, signed=False, nan=U16_NAN, unit="A")
    """MPPT 3 current (reg 5016); excluded on two-MPPT models."""
    total_dc_power = uint32(5016, word_order="little", unit="W")
    """Total DC (PV) power (reg 5017)."""
    phase_a_voltage = gauge(5018, 0.1, signed=False, nan=U16_NAN, unit="V")
    """Phase A voltage, or A-B line voltage on a 3P3L output (reg 5019)."""
    phase_b_voltage = gauge(5019, 0.1, signed=False, nan=U16_NAN, unit="V")
    """Phase B voltage (reg 5020)."""
    phase_c_voltage = gauge(5020, 0.1, signed=False, nan=U16_NAN, unit="V")
    """Phase C voltage (reg 5021)."""
    reactive_power = int32(5032, word_order="little", unit="var")
    """Reactive power (reg 5033)."""
    power_factor = gauge(5034, 0.001)
    """Power factor (reg 5035): positive leading, negative lagging."""
    grid_frequency = gauge(5241, 0.01, signed=False, nan=U16_NAN, unit="Hz")
    """Grid frequency (reg 5242)."""

    @property
    def mppt1_power(self) -> float | None:
        """MPPT 1 power in W, computed from voltage and current."""
        return _power(self.mppt1_voltage, self.mppt1_current)

    @property
    def mppt2_power(self) -> float | None:
        """MPPT 2 power in W, computed from voltage and current."""
        return _power(self.mppt2_voltage, self.mppt2_current)

    @property
    def mppt3_power(self) -> float | None:
        """MPPT 3 power in W, computed from voltage and current."""
        return _power(self.mppt3_voltage, self.mppt3_current)


class Flows(SungrowInput):
    """Running state and the load / grid power flows."""

    running_state_raw = integer(12999, signed=False)
    """Running state word (reg 13000), undecoded."""
    running_state: NumberField[InverterState] = NumberField(
        12999, signed=False, convert=RUNNING_STATES
    )
    """Running state (reg 13000), decoded per Appendix 2."""
    power_flow = flags(13000, PowerFlow)
    """Power flow status bits (reg 13001)."""
    load_power = int32(13007, word_order="little", nan=S32_NAN, unit="W")
    """Load power (reg 13008); valid with a smart meter connected."""
    export_power = int32(13009, word_order="little", nan=S32_NAN, unit="W")
    """Export power (reg 13010): positive = exporting, negative = importing."""


class GridPhases(SungrowInput):
    """Inverter-side phase currents and total active power."""

    phase_a_current = gauge(13030, 0.1, nan=S16_NAN, unit="A")
    """Phase A current (reg 13031)."""
    phase_b_current = gauge(13031, 0.1, nan=S16_NAN, unit="A")
    """Phase B current (reg 13032)."""
    phase_c_current = gauge(13032, 0.1, nan=S16_NAN, unit="A")
    """Phase C current (reg 13033)."""
    total_active_power = int32(13033, word_order="little", unit="W")
    """Total active power at the inverter AC port (reg 13034)."""


class Meter(SungrowInput):
    """The smart meter at the grid connection (Table 3, regs 5601-5608).

    Every value reads None (sentinel 0x7FFFFFFF) without a smart meter, and
    the per-phase values also on a single-phase meter. mkaiser's meter phase
    voltages and currents (5741-5746, undocumented) are not modelled: a
    WiNet-S refuses a read starting there and answers 0 for them inside a
    wider block.
    """

    meter_active_power = int32(5600, word_order="little", nan=S32_NAN, unit="W")
    """Meter active power (reg 5601): positive = importing, negative = exporting."""
    meter_phase_a_active_power = int32(5602, word_order="little", nan=S32_NAN, unit="W")
    """Meter phase A active power (reg 5603)."""
    meter_phase_b_active_power = int32(5604, word_order="little", nan=S32_NAN, unit="W")
    """Meter phase B active power (reg 5605)."""
    meter_phase_c_active_power = int32(5606, word_order="little", nan=S32_NAN, unit="W")
    """Meter phase C active power (reg 5607)."""


class Backup(SungrowInput):
    """The backup (off-grid) port (Table 3, regs 5723-5734)."""

    backup_phase_a_power = integer(5722, unit="W")
    """Phase A backup power (reg 5723)."""
    backup_phase_b_power = integer(5723, unit="W")
    """Phase B backup power (reg 5724)."""
    backup_phase_c_power = integer(5724, unit="W")
    """Phase C backup power (reg 5725)."""
    total_backup_power = int32(5725, word_order="little", unit="W")
    """Total backup power (reg 5726)."""
    backup_phase_a_voltage = gauge(5730, 0.1, signed=False, nan=U16_NAN, unit="V")
    """Phase A backup voltage (reg 5731)."""
    backup_phase_b_voltage = gauge(5731, 0.1, signed=False, nan=U16_NAN, unit="V")
    """Phase B backup voltage (reg 5732)."""
    backup_phase_c_voltage = gauge(5732, 0.1, signed=False, nan=U16_NAN, unit="V")
    """Phase C backup voltage (reg 5733)."""
    backup_frequency = gauge(5733, 0.01, signed=False, nan=U16_NAN, unit="Hz")
    """Backup frequency (reg 5734)."""
