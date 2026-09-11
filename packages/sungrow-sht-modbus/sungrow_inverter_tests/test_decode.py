"""Each component decodes the SH15T-shaped seed as the spec says."""

from __future__ import annotations

import pytest
from modbus_connection.mock import MockModbusUnit

from sungrow_inverter import (
    ChargeCommand,
    EmsMode,
    InverterState,
    OutputType,
    PowerFlow,
)
from sungrow_inverter.components import (
    AcDc,
    Alarms,
    AplShadow,
    Backup,
    Battery,
    BatteryLimits,
    BatteryPower,
    Energy,
    FirmwareInfo,
    Flows,
    GridPhases,
    Identity,
    Meter,
    Ratings,
    Settings,
    StartPower,
)

from .conftest import SERIAL


async def test_identity(unit: MockModbusUnit) -> None:
    identity = Identity(unit)
    await identity.async_update()
    assert identity.protocol_version == 0x01010F00
    assert identity.protocol_version_text == "V1.1.15"
    assert identity.arm_version == "PEARL-H_B000.V000.P099"
    assert identity.dsp_version == "PEARL-H_D000.V000.P099"
    assert identity.serial == SERIAL
    assert identity.device_type_code == 0x0E25
    assert identity.nominal_power == 15000
    assert identity.output_type is OutputType.THREE_PHASE_4_WIRE


async def test_ratings(unit: MockModbusUnit) -> None:
    ratings = Ratings(unit)
    await ratings.async_update()
    assert ratings.export_limit_min == 0
    assert ratings.export_limit_max == 15000
    assert ratings.bdc_rated_power == 15000
    assert ratings.bms_max_charge_current == 200
    assert ratings.bms_max_discharge_current == 200
    assert ratings.battery_capacity == 44.8


async def test_firmware(unit: MockModbusUnit) -> None:
    firmware = FirmwareInfo(unit)
    await firmware.async_update()
    assert firmware.inverter_firmware == "PEARL-H_B000.V000.P099"
    assert firmware.comm_module_firmware == "WINET-SV200.001.00.P020"
    assert firmware.battery_firmware == "SBRHV_B003.V003.P004"
    assert firmware.empty is False


async def test_firmware_blank_strings_count_as_empty(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    firmware = FirmwareInfo(mock_modbus_unit)  # unseeded: all zeros
    await firmware.async_update()
    assert firmware.inverter_firmware == ""
    assert firmware.empty is True


async def test_ac_dc(unit: MockModbusUnit) -> None:
    ac_dc = AcDc(unit)
    await ac_dc.async_update()
    assert ac_dc.mppt1_voltage == 380.0
    assert ac_dc.mppt1_current == 5.2
    assert ac_dc.mppt1_power == 1976.0
    assert ac_dc.mppt3_voltage == 290.0
    assert ac_dc.mppt3_power == 609.0
    assert ac_dc.total_dc_power == 6250
    assert ac_dc.phase_a_voltage == 240.1
    assert ac_dc.reactive_power == -150
    assert ac_dc.power_factor == 0.998
    assert ac_dc.grid_frequency == 50.01


async def test_mppt_sentinel_reads_none(unit: MockModbusUnit) -> None:
    unit.input[5014] = 0xFFFF
    unit.input[5015] = 0xFFFF
    ac_dc = AcDc(unit)
    await ac_dc.async_update()
    assert ac_dc.mppt3_voltage is None
    assert ac_dc.mppt3_power is None


async def test_flows(unit: MockModbusUnit) -> None:
    flows = Flows(unit)
    await flows.async_update()
    assert flows.running_state_raw == 0x8100
    assert flows.running_state is InverterState.DERATING_RUNNING
    assert flows.power_flow == (
        PowerFlow.PV_GENERATING
        | PowerFlow.BATTERY_CHARGING
        | PowerFlow.LOAD_POSITIVE
        | PowerFlow.EXPORTING
    )
    assert flows.load_power == 1850
    assert flows.export_power == 1200


@pytest.mark.parametrize(
    ("word", "state"),
    [
        (0x0000, InverterState.RUNNING),
        (0x0040, InverterState.RUNNING),
        (0x1300, InverterState.KEY_STOP),
        (0x0002, InverterState.KEY_STOP),
        (0x1400, InverterState.STANDBY),
        (0x1600, InverterState.STARTING),
        (0x8200, InverterState.DISPATCH_RUNNING),
        (0x5500, InverterState.FAULT),
        (0x4000, InverterState.EXTERNAL_EMS),
    ],
)
async def test_running_state_codes(
    unit: MockModbusUnit, word: int, state: InverterState
) -> None:
    unit.input[12999] = word
    flows = Flows(unit)
    await flows.async_update()
    assert flows.running_state is state
    assert flows.running_state_raw == word


async def test_unknown_running_state_keeps_the_raw_word(
    unit: MockModbusUnit,
) -> None:
    unit.input[12999] = 0x7777
    flows = Flows(unit)
    await flows.async_update()
    assert flows.running_state is None
    assert flows.running_state_raw == 0x7777


async def test_grid_phases(unit: MockModbusUnit) -> None:
    phases = GridPhases(unit)
    await phases.async_update()
    assert phases.phase_a_current == 2.5
    assert phases.phase_c_current == 2.6
    assert phases.total_active_power == -1450


async def test_meter(unit: MockModbusUnit) -> None:
    meter = Meter(unit)
    await meter.async_update()
    assert meter.meter_active_power == -1200
    assert meter.meter_phase_b_active_power == -400


async def test_no_smart_meter_reads_none_not_two_gigawatts(
    unit: MockModbusUnit,
) -> None:
    """Without a meter the S32 powers carry 0x7FFFFFFF (mkaiser's nan_value)."""
    for address in (5600, 5602, 5604, 5606, 13007, 13009):
        unit.input[address] = [0xFFFF, 0x7FFF]  # low word first
    meter = Meter(unit)
    await meter.async_update()
    assert meter.meter_active_power is None
    assert meter.meter_phase_a_active_power is None
    assert meter.meter_phase_b_active_power is None
    assert meter.meter_phase_c_active_power is None
    flows = Flows(unit)
    await flows.async_update()
    assert flows.load_power is None
    assert flows.export_power is None


async def test_large_and_negative_s32_values_decode(unit: MockModbusUnit) -> None:
    unit.input[13007] = [0x0000, 0x0001]  # 65536 W: needs the high word
    unit.input[13009] = [0xFC18, 0xFFFF]  # -1000 W
    flows = Flows(unit)
    await flows.async_update()
    assert flows.load_power == 65536
    assert flows.export_power == -1000


async def test_backup(unit: MockModbusUnit) -> None:
    backup = Backup(unit)
    await backup.async_update()
    assert backup.backup_phase_a_power == 0
    assert backup.total_backup_power == 0
    assert backup.backup_phase_a_voltage == 241.7
    assert backup.backup_phase_c_voltage == 243.4
    assert backup.backup_frequency == 50.0


async def test_battery(unit: MockModbusUnit) -> None:
    battery = Battery(unit)
    await battery.async_update()
    assert battery.battery_current == -5.2
    assert battery.battery_voltage == 512.3
    assert battery.battery_level == 65.5
    assert battery.battery_soh == 100.0
    assert battery.battery_temperature == 25.0


async def test_battery_power_sign_is_discharge_positive(
    unit: MockModbusUnit,
) -> None:
    power = BatteryPower(unit)
    await power.async_update()
    assert power.battery_power == -2500  # charging
    unit.input[5213] = [3000, 0]
    await power.async_update()
    assert power.battery_power == 3000  # discharging


async def test_energy(unit: MockModbusUnit) -> None:
    energy = Energy(unit)
    await energy.async_update()
    assert energy.daily_output_energy == 12.3
    assert energy.total_output_energy == 4567.8
    assert energy.inverter_temperature == 41.2
    assert energy.daily_pv_generation == 25.1
    assert energy.total_pv_generation == 12345.6
    assert energy.daily_export_from_pv == 5.0
    assert energy.total_battery_charge_from_pv == 444.4
    assert energy.total_direct_consumption == 3333.3
    assert energy.daily_battery_discharge == 3.1
    assert energy.self_consumption_today == 85.0
    assert energy.daily_import == 0.5
    assert energy.total_import == 1111.1
    assert energy.daily_battery_charge == 9.0
    assert energy.total_battery_charge == 460.0
    assert energy.daily_export == 6.0
    assert energy.total_export == 6000.0


async def test_alarms(unit: MockModbusUnit) -> None:
    alarms = Alarms(unit)
    assert alarms.any_active is None
    await alarms.async_update()
    assert alarms.inverter_alarm == 0x0001_0000
    assert alarms.bms_alarm_2 == 0
    assert alarms.any_active is True


async def test_settings(unit: MockModbusUnit) -> None:
    settings = Settings(unit)
    await settings.async_update()
    assert settings.pv_power_limitation is False
    assert settings.ems_mode is EmsMode.SELF_CONSUMPTION
    assert settings.charge_command is ChargeCommand.STOP
    assert settings.forced_power == 0
    assert settings.max_soc == 100.0
    assert settings.min_soc == 5.0
    assert settings.export_limit == 15000
    assert settings.backup_mode is True
    assert settings.export_limit_enabled is False
    assert settings.feed_in_ratio == 100.0
    assert settings.active_power_limit_enabled is False
    assert settings.active_power_limit_ratio == 100.0
    assert settings.backup_reserve_soc == 20


async def test_aa55_decodes_only_the_two_words(
    unit: MockModbusUnit, caplog: pytest.LogCaptureFixture
) -> None:
    unit.holding[13017] = 0xAA
    unit.holding[13074] = 0  # a WiNet-S answers 0 for an unserved register
    unit.holding[13086] = 0xFFFF  # not implemented
    unit.holding[13088] = 0x77  # garbage: None, but worth a warning
    settings = Settings(unit)
    await settings.async_update()
    assert settings.pv_power_limitation is True
    assert settings.backup_mode is None
    assert settings.export_limit_enabled is None
    assert settings.active_power_limit_enabled is None
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "119" in warnings[0].getMessage()


async def test_unknown_ems_mode_reads_none(unit: MockModbusUnit) -> None:
    unit.holding[13049] = 1
    settings = Settings(unit)
    await settings.async_update()
    assert settings.ems_mode is None


async def test_battery_limits_start_power_apl(unit: MockModbusUnit) -> None:
    limits = BatteryLimits(unit)
    await limits.async_update()
    assert limits.max_charge_power == 12000
    assert limits.max_discharge_power == 12000

    start = StartPower(unit)
    await start.async_update()
    assert start.charging_start_power == 70
    assert start.discharging_start_power == 70

    apl = AplShadow(unit)
    await apl.async_update()
    assert apl.apl_shutdown_at_zero is True
