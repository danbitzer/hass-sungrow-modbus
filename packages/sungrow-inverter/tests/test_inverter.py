"""The device object: probe, setup, poll isolation, raw dump."""

from __future__ import annotations

import pytest
from modbus_connection import (
    IllegalDataAddressError,
    ModbusConnectionError,
    ModbusTimeoutError,
    ServerDeviceFailureError,
)
from modbus_connection.mock import MockModbusUnit

from sungrow_inverter import (
    ChargeCommand,
    EmsMode,
    InverterState,
    OutputType,
    PowerFlow,
    SungrowInverter,
    UnsupportedModelError,
)

from .conftest import SERIAL, load_fixture, sh15t_holding, sh15t_input


async def test_probe_reads_identity_only(unit: MockModbusUnit) -> None:
    probe = await SungrowInverter.async_probe(unit)
    assert probe.model.name == "SH15T"
    assert probe.device_type_code == 0x0E25
    assert probe.serial == SERIAL
    assert probe.nominal_power_w == 15000
    assert probe.bdc_rated_power_w == 15000
    assert probe.protocol_version == "V1.1.15"
    assert probe.arm_version == "PEARL-H_B000.V000.P099"
    assert all(b.register_type == "input" for b in unit.read_events)
    assert all(
        4951 <= b.address and b.address + b.count - 1 <= 5638 for b in unit.read_events
    )


async def test_probe_survives_a_refused_ratings_block(unit: MockModbusUnit) -> None:
    unit.fail_read(5627, IllegalDataAddressError(), register_type="input")
    probe = await SungrowInverter.async_probe(unit)
    assert probe.model.name == "SH15T"
    assert probe.bdc_rated_power_w is None


async def test_probe_rejects_a_non_sht_inverter(unit: MockModbusUnit) -> None:
    unit.input[4999] = 0x0E03
    with pytest.raises(UnsupportedModelError, match="SH10RT"):
        await SungrowInverter.async_probe(unit)


async def test_probe_propagates_a_dead_device(unit: MockModbusUnit) -> None:
    unit.fail_requests(ModbusTimeoutError())
    with pytest.raises(ModbusTimeoutError):
        await SungrowInverter.async_probe(unit)


async def test_first_update_runs_setup(inverter: SungrowInverter) -> None:
    assert inverter.is_setup is False
    report = await inverter.async_update_realtime()
    assert inverter.is_setup is True
    assert report.ok
    assert report.updated == [
        "ac_dc",
        "flows",
        "grid_phases",
        "meter",
        "backup",
        "battery",
        "battery_power",
    ]
    assert inverter.model is not None and inverter.model.name == "SH15T"
    assert inverter.identity.serial == SERIAL
    assert inverter.firmware is not None
    assert inverter.firmware.inverter_firmware == "PEARL-H_B000.V000.P099"
    assert inverter.battery.battery_level == 65.5
    assert inverter.battery_power.battery_power == -2500
    # settings is not probed at setup and is not on the realtime list
    assert inverter.settings.ems_mode is None
    assert inverter.alarms is not None  # probed and kept
    assert inverter.ratings is not None
    assert inverter.ratings.battery_capacity == 44.8


async def test_setup_rejects_a_non_sht_inverter(unit: MockModbusUnit) -> None:
    unit.input[4999] = 0x0D1B  # SH10RS
    inverter = SungrowInverter(unit)
    with pytest.raises(UnsupportedModelError, match="SH10RS"):
        await inverter.async_update()
    assert inverter.is_setup is False


async def test_refused_ratings_block_does_not_stop_setup(
    unit: MockModbusUnit,
) -> None:
    unit.fail_read(5638, IllegalDataAddressError(), register_type="input")
    inverter = SungrowInverter(unit)
    report = await inverter.async_update()
    assert report.ok and inverter.is_setup
    assert inverter.ratings is None
    assert inverter.battery_max_power_w == 15000  # falls back to the AC rating
    assert SungrowInverter(unit, battery_max_power_w=12000).battery_max_power_w == (
        12000
    )


async def test_refused_identity_block_stops_setup(unit: MockModbusUnit) -> None:
    unit.fail_read(4999, IllegalDataAddressError(), register_type="input")
    inverter = SungrowInverter(unit)
    with pytest.raises(IllegalDataAddressError):
        await inverter.async_update()
    assert inverter.is_setup is False


async def test_setup_retries_after_an_unreachable_device(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    unit.fail_requests(ModbusConnectionError())
    with pytest.raises(ModbusConnectionError):
        await inverter.async_update_realtime()
    assert inverter.is_setup is False
    unit.fail_requests(None)
    report = await inverter.async_update_realtime()
    assert report.ok and inverter.is_setup


async def test_settings_update(inverter: SungrowInverter) -> None:
    report = await inverter.async_update_settings()
    assert report.ok
    assert report.updated == [
        "settings",
        "battery_limits",
        "energy",
        "start_power",
        "apl_shadow",
        "alarms",
    ]
    assert inverter.settings.export_limit == 15000
    assert inverter.battery_limits.max_charge_power == 12000
    assert inverter.energy.daily_pv_generation == 25.1
    assert inverter.start_power is not None
    assert inverter.start_power.charging_start_power == 70
    assert inverter.apl_shadow is not None
    assert inverter.apl_shadow.apl_shutdown_at_zero is True


async def test_refused_optional_components_are_dropped(
    unit: MockModbusUnit,
) -> None:
    unit.fail_read(13049, IllegalDataAddressError(), register_type="input")
    unit.fail_read(33148, IllegalDataAddressError())
    inverter = SungrowInverter(unit)
    report = await inverter.async_update()
    assert report.ok
    assert inverter.alarms is None
    assert inverter.start_power is None
    assert inverter.apl_shadow is not None
    assert "alarms" not in inverter.polled_components
    assert "start_power" not in inverter.polled_components


async def test_blank_firmware_strings_mean_no_firmware(
    unit: MockModbusUnit,
) -> None:
    for address in (13249, 13264, 13279):
        unit.input[address] = [0] * 15
    inverter = SungrowInverter(unit)
    await inverter.async_update()
    assert inverter.firmware is None
    raw = await inverter.async_read_raw()
    assert 13249 not in raw["input"]


async def test_refused_firmware_block_means_no_firmware(
    unit: MockModbusUnit,
) -> None:
    unit.fail_read(13249, IllegalDataAddressError(), register_type="input")
    inverter = SungrowInverter(unit)
    await inverter.async_update()
    assert inverter.firmware is None


async def test_one_failing_component_does_not_fail_the_poll(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    await inverter.async_update()
    unit.fail_read(5213, ServerDeviceFailureError(), register_type="input")
    report = await inverter.async_update_realtime()
    assert not report.ok
    assert list(report.failed) == ["battery_power"]
    assert isinstance(report.failed["battery_power"], ServerDeviceFailureError)
    assert "battery" in report.updated
    assert inverter.battery.battery_level == 65.5


async def test_connection_loss_is_raised(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    await inverter.async_update()
    unit.fail_read(13007, ModbusConnectionError(), register_type="input")
    with pytest.raises(ModbusConnectionError):
        await inverter.async_update_realtime()


async def test_first_timeout_is_raised_later_ones_recorded(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    await inverter.async_update()
    unit.fail_read(5010, ModbusTimeoutError(), register_type="input")
    with pytest.raises(ModbusTimeoutError):
        await inverter.async_update_realtime()  # ac_dc is the first component
    unit.fail_read(5010, None, register_type="input")
    unit.fail_read(5722, ModbusTimeoutError(), register_type="input")
    report = await inverter.async_update_realtime()
    assert list(report.failed) == ["backup"]
    assert "meter" in report.updated


async def test_listeners_fire_only_for_refreshed_components(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    await inverter.async_update()
    fired: list[str] = []
    inverter.battery.add_update_listener(lambda: fired.append("battery"))
    inverter.battery_power.add_update_listener(lambda: fired.append("battery_power"))
    unit.fail_read(5213, ServerDeviceFailureError(), register_type="input")
    await inverter.async_update_realtime()
    assert fired == ["battery"]


async def test_battery_max_power_defaults_to_bdc_rating(
    unit: MockModbusUnit,
) -> None:
    inverter = SungrowInverter(unit)
    assert inverter.battery_max_power_w is None
    await inverter.async_update()
    assert inverter.battery_max_power_w == 15000
    unit.input[5627] = 300  # a 30 kW BDC on a 15 kW unit: the AC rating caps it
    await inverter.ratings.async_update()  # type: ignore[union-attr]
    assert inverter.battery_max_power_w == 15000
    unit.input[5627] = 100
    await inverter.ratings.async_update()  # type: ignore[union-attr]
    assert inverter.battery_max_power_w == 10000
    assert SungrowInverter(unit, battery_max_power_w=12000).battery_max_power_w == (
        12000
    )


async def test_setup_does_not_fire_listeners(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    fired: list[str] = []
    inverter.identity.add_update_listener(lambda: fired.append("identity"))
    inverter.alarms.add_update_listener(lambda: fired.append("alarms"))  # type: ignore[union-attr]
    inverter.battery.add_update_listener(lambda: fired.append("battery"))
    await inverter.async_update_realtime()
    assert fired == ["battery"]


async def test_refresh_times_are_recorded_per_component(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    assert inverter.last_refresh("settings") is None
    report = await inverter.async_update_settings()
    stamp = inverter.last_refresh("settings")
    assert stamp is not None and stamp >= report.at
    assert inverter.last_refresh("identity") is not None
    assert inverter.last_refresh("ac_dc") is None  # not polled yet
    unit.fail_read(13073, ServerDeviceFailureError())
    await inverter.async_update_settings()
    assert inverter.last_refresh("settings") == stamp  # a failed read keeps the old
    assert inverter.last_refresh("battery_limits") != stamp


async def test_collect_raw_fills_the_report_in_one_sweep(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    await inverter.async_update()
    unit.read_events.clear()
    report = await inverter.async_update(collect_raw=True)
    assert report.ok and report.raw is not None
    assert report.raw["input"][13022] == 655
    assert report.raw["holding"][33046] == 1200
    assert 4999 not in report.raw["input"]  # setup blocks are not re-read
    assert len(unit.read_events) == 9 + 7
    assert inverter.battery.battery_level == 65.5  # the fields refreshed too
    assert (await inverter.async_update()).raw is None


async def test_read_raw_covers_every_polled_register(
    inverter: SungrowInverter,
) -> None:
    raw = await inverter.async_read_raw()
    assert set(raw) == {"input", "holding"}
    assert raw["input"][4999] == 0x0E25
    assert raw["input"][5213] == (-2500) & 0xFFFF
    assert raw["holding"][13017] == 0x55
    assert raw["holding"][33046] == 1200
    assert list(raw["input"]) == sorted(raw["input"])
    assert 12999 not in raw["holding"]  # the control register is never read
    assert raw["input"][5638] == 4480  # ratings block included


async def test_refresh_reads_only_the_named_components(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    await inverter.async_update()
    unit.read_events.clear()
    fired: list[str] = []
    inverter.settings.add_update_listener(lambda: fired.append("settings"))
    inverter.energy.add_update_listener(lambda: fired.append("energy"))
    report = await inverter.async_refresh("settings", "battery_limits")
    assert report.updated == ["settings", "battery_limits"]
    assert [e.address for e in unit.read_events] == [13017, 33046]
    assert fired == ["settings"]
    assert inverter.last_refresh("settings") is not None
    with pytest.raises(ValueError, match="identity"):
        await inverter.async_refresh("identity")


async def test_read_raw_leaves_out_a_failing_component(
    unit: MockModbusUnit, inverter: SungrowInverter
) -> None:
    await inverter.async_update()
    unit.fail_read(5213, ServerDeviceFailureError(), register_type="input")
    raw = await inverter.async_read_raw()
    assert 5213 not in raw["input"]
    assert raw["input"][13022] == 655


async def test_raw_dump_replays_through_the_mock(
    unit: MockModbusUnit, mock_modbus_connection: object
) -> None:
    raw = await SungrowInverter(unit).async_read_raw()
    from modbus_connection.mock import MockModbusConnection

    replay = MockModbusConnection().for_unit(1)
    replay.load_raw(raw)
    inverter = SungrowInverter(replay)
    report = await inverter.async_update()
    assert report.ok
    assert inverter.identity.serial == SERIAL
    assert inverter.flows.load_power == 1850
    assert inverter.settings.backup_reserve_soc == 20


@pytest.mark.parametrize("name", ["sh15t_seed.json", "sh15t_p063.json"])
async def test_committed_fixture_decodes(
    mock_modbus_unit: MockModbusUnit, name: str
) -> None:
    mock_modbus_unit.load_raw(load_fixture(name))
    inverter = SungrowInverter(mock_modbus_unit)
    report = await inverter.async_update()
    assert report.ok
    assert inverter.model is not None and inverter.model.name == "SH15T"
    assert inverter.identity.serial == SERIAL


async def test_live_capture_decodes_as_the_mkaiser_entities_showed(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    """The SH15T capture (firmware P063, WiNet-S V300) against HA at the time.

    Captured under the current ranges; live values are the moment's, the
    static ones matched the mkaiser entities exactly.
    """
    mock_modbus_unit.load_raw(load_fixture("sh15t_p063.json"))
    inverter = SungrowInverter(mock_modbus_unit)
    assert (await inverter.async_update()).ok
    assert inverter.identity.protocol_version_text == "V1.1.7"
    assert inverter.identity.arm_version == "ARM_PEARL-H_V11_V01_A"
    assert inverter.identity.dsp_version == "MDSP_PEARL-H_V11_V01_A"
    assert inverter.identity.nominal_power == 15000
    assert inverter.identity.output_type is OutputType.THREE_PHASE_4_WIRE
    assert inverter.ratings is not None
    assert inverter.ratings.bdc_rated_power == 30000  # twice the AC rating
    assert inverter.ratings.export_limit_max == 15000
    assert inverter.ratings.battery_capacity == 44.8
    assert inverter.ratings.bms_max_discharge_current == 30
    assert inverter.battery_max_power_w == 15000  # capped by the AC rating
    assert inverter.firmware is not None
    assert inverter.firmware.inverter_firmware == "PEARL-H_B000.V000.P063"
    assert inverter.firmware.comm_module_firmware == "WINET-SV300.001.03.P029"
    assert inverter.flows.running_state is InverterState.DISPATCH_RUNNING
    assert inverter.flows.running_state_raw == 0x8200
    assert inverter.flows.power_flow == (
        PowerFlow.BATTERY_DISCHARGING | PowerFlow.LOAD_POSITIVE
    )
    assert inverter.flows.load_power == 839
    assert inverter.flows.export_power == 10
    assert inverter.meter.meter_active_power == -10  # negative = selling
    assert inverter.meter.meter_phase_b_active_power == 320
    assert inverter.battery_power.battery_power == 928  # positive = discharging
    assert inverter.battery.battery_level == 97.8
    assert inverter.battery.battery_voltage == 464.2
    assert inverter.battery.battery_current == 2.0
    assert inverter.ac_dc.mppt1_voltage == 99.4
    assert inverter.ac_dc.mppt3_voltage == 100.6
    assert inverter.ac_dc.total_dc_power == 0
    assert inverter.ac_dc.reactive_power == 2686
    assert inverter.ac_dc.grid_frequency == 50.01
    assert inverter.grid_phases.total_active_power == 829
    assert inverter.grid_phases.phase_a_current == 4.8  # energy block re-read it
    assert inverter.backup.total_backup_power == 851
    assert inverter.backup.backup_phase_b_power == 618
    assert inverter.backup.backup_phase_a_voltage == 245.5
    assert inverter.backup.backup_frequency == 49.99
    assert inverter.energy.total_pv_generation == 5009.6
    assert inverter.energy.daily_pv_generation == 28.5
    assert inverter.energy.total_import == 95.1
    assert inverter.energy.total_export == 2491.6
    assert inverter.energy.total_battery_charge == 2170.3
    assert inverter.energy.inverter_temperature == 42.0
    assert inverter.energy.self_consumption_today == 30.8
    assert inverter.settings.ems_mode is EmsMode.SELF_CONSUMPTION
    assert inverter.settings.charge_command is ChargeCommand.DISCHARGE  # leftover
    assert inverter.settings.forced_power == 10000
    assert inverter.settings.export_limit == 15000
    assert inverter.settings.export_limit_enabled is True
    assert inverter.settings.pv_power_limitation is False
    assert inverter.settings.backup_reserve_soc == 5
    assert inverter.settings.max_soc == 100.0 and inverter.settings.min_soc == 5.0
    assert inverter.battery_limits.max_charge_power == 10000
    assert inverter.battery_limits.max_discharge_power == 10000
    assert inverter.start_power is not None  # served, but as 0xFFFF
    assert inverter.start_power.charging_start_power is None
    assert inverter.apl_shadow is not None
    assert inverter.apl_shadow.apl_shutdown_at_zero is True
    assert inverter.alarms is not None and inverter.alarms.any_active is False
    # the reserved registers inside the settings block: 0xFFFF, except two
    raw = load_fixture("sh15t_p063.json")["holding"]
    assert raw[13059] == 0xFFFF and raw[13075] == 0xFFFF and raw[13090] == 0xFFFF
    assert raw[13052] == 0 and raw[13079] == 0


def test_seed_helpers_are_deterministic() -> None:
    assert sh15t_input() == sh15t_input()
    assert sh15t_holding() == sh15t_holding()


@pytest.mark.parametrize("name", ["sh15t_seed.json", "sh15t_p063.json"])
async def test_fixture_covers_every_planned_block(
    mock_modbus_unit: MockModbusUnit, name: str
) -> None:
    """A fixture captured under narrower ranges would replay holes as 0.

    Every address of every block the current plan reads must be in the dump,
    so a replay decodes exactly what the device answered.
    """
    raw = load_fixture(name)
    mock_modbus_unit.load_raw(raw)
    inverter = SungrowInverter(mock_modbus_unit)
    assert (await inverter.async_update()).ok
    missing: list[str] = []
    for event in mock_modbus_unit.read_events:
        space = "holding" if event.register_type == "holding" else "input"
        for address in range(event.address, event.address + event.count):
            if address not in raw[space]:
                missing.append(f"{space}:{address}")
    assert missing == []
