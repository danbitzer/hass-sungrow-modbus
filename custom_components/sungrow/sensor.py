"""Sensors: measurements, energy counters, state and diagnostic mirrors.

Keys mirror the mkaiser YAML package's object ids where the value is the
same register, so a user migrating can rename entities onto the ids their
history lives under (unit classes and state classes match those entries).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from sungrow_inverter import BatteryMode, InverterState, SungrowInverter

from . import SungrowConfigEntry
from .const import REALTIME, SETTINGS
from .entity import SungrowEntity, SungrowEntityDescription

type ValueFn = Callable[[SungrowInverter], StateType]


@dataclass(frozen=True, kw_only=True)
class SungrowSensorDescription(SungrowEntityDescription, SensorEntityDescription):
    """A sensor backed by one device attribute."""

    value_fn: ValueFn
    attributes_fn: Callable[[SungrowInverter], dict[str, Any]] | None = None


def _mppt3(device: SungrowInverter) -> bool:
    return device.model is not None and device.model.mppt >= 3


def _firmware(device: SungrowInverter) -> bool:
    return device.firmware is not None


def _ratings(device: SungrowInverter) -> bool:
    return device.ratings is not None


def _start_power(device: SungrowInverter) -> bool:
    return device.start_power is not None


def _alarms(device: SungrowInverter) -> bool:
    return device.alarms is not None


def _positive(value: float | None) -> float | None:
    return None if value is None else max(value, 0)


def _negative(value: float | None) -> float | None:
    return None if value is None else max(-value, 0)


def _mode_attributes(device: SungrowInverter) -> dict[str, Any]:
    s, lim = device.settings, device.battery_limits
    return {
        "ems_mode": s.ems_mode.name.lower() if s.ems_mode is not None else None,
        "charge_command": (
            s.charge_command.name.lower() if s.charge_command is not None else None
        ),
        "forced_power_w": s.forced_power,
        "max_charge_power_w": lim.max_charge_power,
        "max_discharge_power_w": lim.max_discharge_power,
        "export_limit_w": s.export_limit,
        "export_limit_enabled": s.export_limit_enabled,
        "pv_limited": s.pv_power_limitation,
        "battery_max_power_w": device.battery_max_power_w,
    }


def _power(key: str, report: str, fn: ValueFn, **kw: Any) -> SungrowSensorDescription:
    return SungrowSensorDescription(
        key=key,
        translation_key=key,
        report_name=report,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=fn,
        **kw,
    )


def _voltage(key: str, report: str, fn: ValueFn, **kw: Any) -> SungrowSensorDescription:
    return SungrowSensorDescription(
        key=key,
        translation_key=key,
        report_name=report,
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=fn,
        **kw,
    )


def _current(key: str, report: str, fn: ValueFn, **kw: Any) -> SungrowSensorDescription:
    return SungrowSensorDescription(
        key=key,
        translation_key=key,
        report_name=report,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=fn,
        **kw,
    )


def _energy(key: str, fn: ValueFn, *, total: bool) -> SungrowSensorDescription:
    return SungrowSensorDescription(
        key=key,
        translation_key=key,
        report_name="energy",
        poll=SETTINGS,
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL
        if total
        else SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=1,
        value_fn=fn,
    )


def _diag(
    key: str, report: str | None, fn: ValueFn, **kw: Any
) -> SungrowSensorDescription:
    return SungrowSensorDescription(
        key=key,
        translation_key=key,
        report_name=report,
        poll=SETTINGS,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=fn,
        **kw,
    )


SENSORS: tuple[SungrowSensorDescription, ...] = (
    # -- power (realtime) ----------------------------------------------------
    _power("total_dc_power", "ac_dc", lambda d: d.ac_dc.total_dc_power),
    _power("mppt1_power", "ac_dc", lambda d: d.ac_dc.mppt1_power),
    _power("mppt2_power", "ac_dc", lambda d: d.ac_dc.mppt2_power),
    _power("mppt3_power", "ac_dc", lambda d: d.ac_dc.mppt3_power, exists=_mppt3),
    _power(
        "total_active_power", "grid_phases", lambda d: d.grid_phases.total_active_power
    ),
    SungrowSensorDescription(
        key="reactive_power",
        translation_key="reactive_power",
        report_name="ac_dc",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.ac_dc.reactive_power,
    ),
    _power("battery_power", "battery_power", lambda d: d.battery_power.battery_power),
    _power(
        "battery_charging_power",
        "battery_power",
        lambda d: _negative(d.battery_power.battery_power),
    ),
    _power(
        "battery_discharging_power",
        "battery_power",
        lambda d: _positive(d.battery_power.battery_power),
    ),
    _power("load_power", "flows", lambda d: d.flows.load_power),
    _power("export_power_raw", "flows", lambda d: d.flows.export_power),
    _power("export_power", "flows", lambda d: _positive(d.flows.export_power)),
    _power("import_power", "flows", lambda d: _negative(d.flows.export_power)),
    _power("meter_active_power", "meter", lambda d: d.meter.meter_active_power),
    _power(
        "meter_phase_a_active_power",
        "meter",
        lambda d: d.meter.meter_phase_a_active_power,
    ),
    _power(
        "meter_phase_b_active_power",
        "meter",
        lambda d: d.meter.meter_phase_b_active_power,
    ),
    _power(
        "meter_phase_c_active_power",
        "meter",
        lambda d: d.meter.meter_phase_c_active_power,
    ),
    _power("backup_phase_a_power", "backup", lambda d: d.backup.backup_phase_a_power),
    _power("backup_phase_b_power", "backup", lambda d: d.backup.backup_phase_b_power),
    _power("backup_phase_c_power", "backup", lambda d: d.backup.backup_phase_c_power),
    _power("total_backup_power", "backup", lambda d: d.backup.total_backup_power),
    # -- electrical (realtime) -------------------------------------------------
    _voltage("mppt1_voltage", "ac_dc", lambda d: d.ac_dc.mppt1_voltage),
    _current("mppt1_current", "ac_dc", lambda d: d.ac_dc.mppt1_current),
    _voltage("mppt2_voltage", "ac_dc", lambda d: d.ac_dc.mppt2_voltage),
    _current("mppt2_current", "ac_dc", lambda d: d.ac_dc.mppt2_current),
    _voltage("mppt3_voltage", "ac_dc", lambda d: d.ac_dc.mppt3_voltage, exists=_mppt3),
    _current("mppt3_current", "ac_dc", lambda d: d.ac_dc.mppt3_current, exists=_mppt3),
    _voltage("phase_a_voltage", "ac_dc", lambda d: d.ac_dc.phase_a_voltage),
    _voltage("phase_b_voltage", "ac_dc", lambda d: d.ac_dc.phase_b_voltage),
    _voltage("phase_c_voltage", "ac_dc", lambda d: d.ac_dc.phase_c_voltage),
    _current("phase_a_current", "grid_phases", lambda d: d.grid_phases.phase_a_current),
    _current("phase_b_current", "grid_phases", lambda d: d.grid_phases.phase_b_current),
    _current("phase_c_current", "grid_phases", lambda d: d.grid_phases.phase_c_current),
    _voltage(
        "backup_phase_a_voltage", "backup", lambda d: d.backup.backup_phase_a_voltage
    ),
    _voltage(
        "backup_phase_b_voltage", "backup", lambda d: d.backup.backup_phase_b_voltage
    ),
    _voltage(
        "backup_phase_c_voltage", "backup", lambda d: d.backup.backup_phase_c_voltage
    ),
    SungrowSensorDescription(
        key="backup_frequency",
        translation_key="backup_frequency",
        report_name="backup",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.backup.backup_frequency,
    ),
    _voltage("battery_voltage", "battery", lambda d: d.battery.battery_voltage),
    _current("battery_current", "battery", lambda d: d.battery.battery_current),
    SungrowSensorDescription(
        key="grid_frequency",
        translation_key="grid_frequency",
        report_name="ac_dc",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda d: d.ac_dc.grid_frequency,
    ),
    SungrowSensorDescription(
        key="power_factor",
        translation_key="power_factor",
        report_name="ac_dc",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
        value_fn=lambda d: d.ac_dc.power_factor,
    ),
    # -- battery (realtime) ----------------------------------------------------
    SungrowSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        report_name="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.battery.battery_level,
    ),
    SungrowSensorDescription(
        key="battery_state_of_health",
        translation_key="battery_state_of_health",
        report_name="battery",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: d.battery.battery_soh,
    ),
    SungrowSensorDescription(
        key="battery_temperature",
        translation_key="battery_temperature",
        report_name="battery",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.battery.battery_temperature,
    ),
    SungrowSensorDescription(
        key="inverter_temperature",
        translation_key="inverter_temperature",
        report_name="energy",
        poll=SETTINGS,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.energy.inverter_temperature,
    ),
    # -- state ---------------------------------------------------------------
    SungrowSensorDescription(
        key="inverter_state",
        translation_key="inverter_state",
        report_name="flows",
        device_class=SensorDeviceClass.ENUM,
        options=[state.value for state in InverterState],
        value_fn=lambda d: (
            d.flows.running_state.value if d.flows.running_state is not None else None
        ),
    ),
    SungrowSensorDescription(
        key="running_state_raw",
        translation_key="running_state_raw",
        report_name="flows",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.flows.running_state_raw,
    ),
    SungrowSensorDescription(
        key="power_flow_status",
        translation_key="power_flow_status",
        report_name="flows",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (
            int(d.flows.power_flow) if d.flows.power_flow is not None else None
        ),
    ),
    SungrowSensorDescription(
        key="battery_mode",
        translation_key="battery_mode",
        report_name="settings",
        poll=SETTINGS,
        device_class=SensorDeviceClass.ENUM,
        options=[mode.value for mode in BatteryMode],
        value_fn=lambda d: (
            d.effective_battery_mode.value
            if d.effective_battery_mode is not None
            else None
        ),
        attributes_fn=_mode_attributes,
    ),
    SungrowSensorDescription(
        key="self_consumption_today",
        translation_key="self_consumption_today",
        report_name="energy",
        poll=SETTINGS,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: d.energy.self_consumption_today,
    ),
    # -- energy (settings poll, restored) --------------------------------------
    _energy("daily_pv_generation", lambda d: d.energy.daily_pv_generation, total=False),
    _energy("total_pv_generation", lambda d: d.energy.total_pv_generation, total=True),
    _energy(
        "daily_exported_energy_from_pv",
        lambda d: d.energy.daily_export_from_pv,
        total=False,
    ),
    _energy(
        "total_exported_energy_from_pv",
        lambda d: d.energy.total_export_from_pv,
        total=True,
    ),
    _energy(
        "daily_battery_charge_from_pv",
        lambda d: d.energy.daily_battery_charge_from_pv,
        total=False,
    ),
    _energy(
        "total_battery_charge_from_pv",
        lambda d: d.energy.total_battery_charge_from_pv,
        total=True,
    ),
    _energy(
        "daily_direct_energy_consumption",
        lambda d: d.energy.daily_direct_consumption,
        total=False,
    ),
    _energy(
        "total_direct_energy_consumption",
        lambda d: d.energy.total_direct_consumption,
        total=True,
    ),
    _energy(
        "daily_battery_discharge",
        lambda d: d.energy.daily_battery_discharge,
        total=False,
    ),
    _energy(
        "total_battery_discharge",
        lambda d: d.energy.total_battery_discharge,
        total=True,
    ),
    _energy("daily_imported_energy", lambda d: d.energy.daily_import, total=False),
    _energy("total_imported_energy", lambda d: d.energy.total_import, total=True),
    _energy(
        "daily_battery_charge", lambda d: d.energy.daily_battery_charge, total=False
    ),
    _energy(
        "total_battery_charge", lambda d: d.energy.total_battery_charge, total=True
    ),
    _energy("daily_exported_energy", lambda d: d.energy.daily_export, total=False),
    _energy("total_exported_energy", lambda d: d.energy.total_export, total=True),
    _energy(
        "daily_pv_generation_battery_discharge",
        lambda d: d.energy.daily_output_energy,
        total=False,
    ),
    _energy(
        "total_pv_generation_battery_discharge",
        lambda d: d.energy.total_output_energy,
        total=True,
    ),
    # -- diagnostics: settings and ratings mirrors ----------------------------
    _diag(
        "export_power_limit",
        "settings",
        lambda d: d.settings.export_limit,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    _diag(
        "feed_in_limitation_ratio",
        "settings",
        lambda d: d.settings.feed_in_ratio,
        native_unit_of_measurement=PERCENTAGE,
    ),
    _diag(
        "active_power_limitation_ratio",
        "settings",
        lambda d: d.settings.active_power_limit_ratio,
        native_unit_of_measurement=PERCENTAGE,
    ),
    _diag(
        "battery_max_charge_power",
        "battery_limits",
        lambda d: d.battery_limits.max_charge_power,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    _diag(
        "battery_max_discharge_power",
        "battery_limits",
        lambda d: d.battery_limits.max_discharge_power,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    _diag(
        "battery_charging_start_power",
        "start_power",
        lambda d: d.start_power.charging_start_power if d.start_power else None,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        exists=_start_power,
    ),
    _diag(
        "battery_discharging_start_power",
        "start_power",
        lambda d: d.start_power.discharging_start_power if d.start_power else None,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        exists=_start_power,
    ),
    _diag(
        "export_power_limit_min",
        None,
        lambda d: d.ratings.export_limit_min if d.ratings else None,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        exists=_ratings,
    ),
    _diag(
        "export_power_limit_max",
        None,
        lambda d: d.ratings.export_limit_max if d.ratings else None,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        exists=_ratings,
    ),
    _diag(
        "bdc_rated_power",
        None,
        lambda d: d.ratings.bdc_rated_power if d.ratings else None,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        exists=_ratings,
    ),
    _diag(
        "bms_max_charging_current",
        None,
        lambda d: d.ratings.bms_max_charge_current if d.ratings else None,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        exists=_ratings,
    ),
    _diag(
        "bms_max_discharging_current",
        None,
        lambda d: d.ratings.bms_max_discharge_current if d.ratings else None,
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        exists=_ratings,
    ),
    _diag(
        "battery_capacity_high_precision",
        None,
        lambda d: d.ratings.battery_capacity if d.ratings else None,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=2,
        exists=_ratings,
    ),
    _diag(
        "inverter_rated_output",
        None,
        lambda d: d.identity.nominal_power,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
    ),
    _diag("sungrow_device_type_code", None, lambda d: d.identity.device_type_code),
    _diag(
        "sungrow_device_type",
        None,
        lambda d: d.model.name if d.model is not None else None,
    ),
    _diag("sungrow_protocol_version", None, lambda d: d.identity.protocol_version_text),
    _diag("sungrow_arm_software", None, lambda d: d.identity.arm_version),
    _diag("sungrow_dsp_software", None, lambda d: d.identity.dsp_version),
    _diag(
        "inverter_firmware_version",
        None,
        lambda d: d.firmware.inverter_firmware if d.firmware else None,
        exists=_firmware,
    ),
    _diag(
        "communication_module_firmware_version",
        None,
        lambda d: d.firmware.comm_module_firmware if d.firmware else None,
        exists=_firmware,
    ),
    _diag(
        "battery_firmware_version",
        None,
        lambda d: d.firmware.battery_firmware if d.firmware else None,
        exists=_firmware,
    ),
)

ALARM_WORDS: tuple[str, ...] = (
    "inverter_alarm",
    "grid_fault",
    "system_fault_1",
    "system_fault_2",
    "dc_fault",
    "permanent_fault",
    "bdc_fault",
    "bdc_permanent_fault",
    "battery_fault",
    "battery_alarm",
    "bms_alarm",
    "bms_protection",
    "bms_fault_1",
    "bms_fault_2",
    "bms_alarm_2",
)


def _alarm_word(name: str) -> SungrowSensorDescription:
    return _diag(
        f"alarm_{name}",
        "alarms",
        lambda d, n=name: getattr(d.alarms, n) if d.alarms else None,  # type: ignore[misc]
        entity_registry_enabled_default=False,
        exists=_alarms,
    )


ALARM_SENSORS: tuple[SungrowSensorDescription, ...] = tuple(
    _alarm_word(name) for name in ALARM_WORDS
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SungrowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensors this inverter serves."""
    runtime = entry.runtime_data
    coordinators = {REALTIME: runtime.realtime, SETTINGS: runtime.settings}
    device = runtime.device
    entities: list[SensorEntity] = []
    for description in (*SENSORS, *ALARM_SENSORS):
        if not description.exists(device):
            continue
        coordinator = coordinators[description.poll]
        if description.state_class in (
            SensorStateClass.TOTAL,
            SensorStateClass.TOTAL_INCREASING,
        ):
            entities.append(SungrowTotalSensor(coordinator, description))
        else:
            entities.append(SungrowSensor(coordinator, description))
    async_add_entities(entities)


class SungrowSensor(SungrowEntity, SensorEntity):
    """A measurement or state read off the device."""

    entity_description: SungrowSensorDescription

    @property
    def native_value(self) -> StateType:
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attributes_fn
        return fn(self.device) if fn is not None else None


class SungrowTotalSensor(SungrowEntity, RestoreSensor):
    """A long-term statistic: holds its last value, and may outlive the device."""

    entity_description: SungrowSensorDescription

    @property
    def available(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last := await self.async_get_last_sensor_data()) is not None:
            self._attr_native_value = last.native_value
        self._process_data()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._process_data()
        super()._handle_coordinator_update()

    def _process_data(self) -> None:
        name = self.entity_description.report_name
        data = self.coordinator.data
        if data is not None and name is not None and name not in data.updated:
            return  # the component did not refresh; keep the last value
        value = self.entity_description.value_fn(self.device)
        if value is None:
            return
        last = self._attr_native_value
        if (
            self.entity_description.state_class is SensorStateClass.TOTAL_INCREASING
            and isinstance(last, (int, float))
            and isinstance(value, (int, float))
            and last * 0.99 <= value < last
        ):
            return  # a tiny dip is a firmware quirk, not a reset
        self._attr_native_value = value
