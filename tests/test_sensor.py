"""Sensors and binary sensors against the live capture."""

from __future__ import annotations

import logging

import pytest
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from modbus_connection import ServerDeviceFailureError
from modbus_connection.mock import MockModbusUnit
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)

from custom_components.sungrow.const import DOMAIN

from .conftest import ENTRY_DATA, ENTRY_OPTIONS, SERIAL, setup_entry

COMPULSORY_RUNNING = 0x0800  # running state while a forced mode is active


def entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SERIAL}_{key}")
    assert found is not None, key
    return found


def state(hass: HomeAssistant, platform: str, key: str) -> str:
    found = hass.states.get(entity_id(hass, platform, key))
    assert found is not None, key
    return found.state


async def test_values_match_the_capture(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    assert state(hass, "sensor", "battery_level") == "97.8"
    assert state(hass, "sensor", "battery_power") == "928"  # positive = discharging
    assert state(hass, "sensor", "battery_discharging_power") == "928"
    assert state(hass, "sensor", "battery_charging_power") == "0"
    assert state(hass, "sensor", "load_power") == "839"
    assert state(hass, "sensor", "export_power_raw") == "10"
    assert state(hass, "sensor", "export_power") == "10"
    assert state(hass, "sensor", "import_power") == "0"
    assert state(hass, "sensor", "total_dc_power") == "0"
    assert state(hass, "sensor", "mppt3_voltage") == "100.6"  # a 3-MPPT model
    assert state(hass, "sensor", "grid_frequency") == "50.01"
    assert state(hass, "sensor", "reactive_power") == "2686"
    assert state(hass, "sensor", "inverter_state") == "dispatch_running"
    assert state(hass, "sensor", "battery_mode") == "self_consumption"
    assert state(hass, "sensor", "total_pv_generation") == "5009.6"
    assert state(hass, "sensor", "daily_pv_generation") == "28.5"
    assert state(hass, "sensor", "battery_capacity_high_precision") == "44.8"
    assert (
        state(hass, "sensor", "inverter_firmware_version") == "PEARL-H_B000.V000.P063"
    )
    assert state(hass, "sensor", "sungrow_device_type") == "SH15T"
    assert state(hass, "sensor", "feed_in_limitation_ratio") == "100.0"
    assert state(hass, "sensor", "battery_charging_start_power") == "unknown"  # 0xFFFF
    assert state(hass, "sensor", "self_consumption_today") == "30.8"

    mode = hass.states.get(entity_id(hass, "sensor", "battery_mode"))
    assert mode is not None
    assert mode.attributes["charge_command"] == "discharge"  # the leftover
    assert mode.attributes["max_charge_power_w"] == 10000
    assert mode.attributes["battery_max_power_w"] == 10000
    assert mode.attributes["export_limit_enabled"] is True

    assert state(hass, "binary_sensor", "battery_discharging") == "on"
    assert state(hass, "binary_sensor", "battery_charging") == "off"
    assert state(hass, "binary_sensor", "pv_generating") == "off"
    assert state(hass, "binary_sensor", "grid_connected") == "on"
    assert state(hass, "binary_sensor", "inverter_alarm_active") == "off"
    assert state(hass, "binary_sensor", "apl_shutdown_at_zero") == "on"


async def test_unit_classes_match_the_mkaiser_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The history-preservation contract: unit and state class of ported ids."""
    await setup_entry(hass, config_entry)
    power = ("W", "power", "measurement")
    total = ("kWh", "energy", "total")
    daily = ("kWh", "energy", "total_increasing")
    expected = {
        "battery_level": ("%", "battery", "measurement"),
        "battery_power": power,
        "battery_charging_power": power,
        "battery_discharging_power": power,
        "load_power": power,
        "export_power": power,
        "import_power": power,
        "total_dc_power": power,
        "total_pv_generation": total,
        "daily_pv_generation": daily,
        "total_imported_energy": total,
        "total_exported_energy": total,
        "total_battery_charge": total,
        "daily_battery_charge": daily,
        "total_battery_discharge": total,
        "daily_battery_discharge": daily,
        "daily_exported_energy_from_pv": daily,
        "total_exported_energy_from_pv": total,
        "daily_direct_energy_consumption": daily,
        "total_direct_energy_consumption": total,
        "daily_battery_charge_from_pv": daily,
        "total_battery_charge_from_pv": total,
        "self_consumption_today": ("%", None, "measurement"),
        "battery_state_of_health": ("%", None, "measurement"),
        "battery_temperature": ("°C", "temperature", "measurement"),
        "grid_frequency": ("Hz", "frequency", "measurement"),
        "battery_capacity_high_precision": ("kWh", "energy_storage", None),
    }
    for key, (unit, device_class, state_class) in expected.items():
        found = hass.states.get(entity_id(hass, "sensor", key))
        assert found is not None, key
        assert found.attributes.get("unit_of_measurement") == unit, key
        assert found.attributes.get("device_class") == device_class, key
        assert found.attributes.get("state_class") == state_class, key


async def test_dead_daily_grid_counters_are_disabled_by_default(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """Regs 13036/13045 never move on an SH15T (P063) while the totals do."""
    await setup_entry(hass, config_entry)
    registry = er.async_get(hass)
    for key in ("daily_imported_energy", "daily_exported_energy"):
        entry = registry.async_get(entity_id(hass, "sensor", key))
        assert entry is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert state(hass, "sensor", "total_imported_energy") == "95.1"


async def test_a_failed_component_takes_only_its_own_entities_down(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    mock_unit.fail_read(5213, ServerDeviceFailureError(), register_type="input")
    await runtime.realtime.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, "sensor", "battery_power") == STATE_UNAVAILABLE
    assert state(hass, "sensor", "battery_level") == "97.8"


async def test_energy_totals_hold_their_value_when_the_block_fails(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    mock_unit.fail_read(13001, ServerDeviceFailureError(), register_type="input")
    await runtime.settings.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, "sensor", "total_pv_generation") == "5009.6"
    assert state(hass, "sensor", "inverter_temperature") == STATE_UNAVAILABLE


async def test_two_mppt_model_has_no_mppt3_entities(
    hass: HomeAssistant, mock_unit: MockModbusUnit
) -> None:
    mock_unit.input[4999] = 0x0E23  # SH10T
    config_entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sungrow SH10T",
        unique_id=SERIAL,
        data={**ENTRY_DATA, "device_type_code": 0x0E23},
        options=ENTRY_OPTIONS,
    )
    await setup_entry(hass, config_entry)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_mppt2_voltage")
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, f"{SERIAL}_mppt3_voltage")
        is None
    )


async def test_grid_connected_needs_both_its_components(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    """The voltage comes from ac_dc, the running state from flows."""
    await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    mock_unit.fail_read(5010, ServerDeviceFailureError(), register_type="input")
    await runtime.realtime.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, "sensor", "phase_a_voltage") == STATE_UNAVAILABLE
    assert state(hass, "binary_sensor", "grid_connected") == STATE_UNAVAILABLE
    assert state(hass, "binary_sensor", "battery_discharging") == "on"


async def test_battery_mode_needs_the_limits_too(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    mock_unit.fail_read(33046, ServerDeviceFailureError(), register_type="holding")
    await runtime.settings.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, "sensor", "battery_mode") == STATE_UNAVAILABLE
    assert state(hass, "sensor", "feed_in_limitation_ratio") == "100.0"


async def test_inconsistent_battery_mode_is_a_state_and_logs_once(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_unit: MockModbusUnit,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An unserved EMS word reads self-consumption while the inverter runs a
    forced mode: distinguishable from "not read yet", and not a log flood."""
    await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    mock_unit.input[12999] = COMPULSORY_RUNNING
    await runtime.realtime.async_refresh()
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        for _ in range(3):
            await runtime.settings.async_refresh()
            await hass.async_block_till_done()
    assert state(hass, "sensor", "battery_mode") == "inconsistent"
    assert caplog.text.count("inconsistent") == 1
    assert "running state compulsory" in caplog.text or "compulsory" in caplog.text

    mock_unit.input[12999] = 0x8200  # dispatch running
    await runtime.realtime.async_refresh()
    with caplog.at_level(logging.INFO):
        await runtime.settings.async_refresh()
        await hass.async_block_till_done()
    assert state(hass, "sensor", "battery_mode") == "self_consumption"
    assert "consistent again" in caplog.text


async def test_energy_totals_restore_across_a_restart(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    """A total whose block is unreadable at startup keeps its restored value."""
    mock_restore_cache_with_extra_data(
        hass,
        (
            (
                State("sensor.sungrow_sh15t_total_pv_generation", "1234.5"),
                {"native_value": 1234.5, "native_unit_of_measurement": "kWh"},
            ),
        ),
    )
    mock_unit.fail_read(13001, ServerDeviceFailureError(), register_type="input")
    await setup_entry(hass, config_entry)
    assert state(hass, "sensor", "total_pv_generation") == "1234.5"
    assert state(hass, "sensor", "inverter_temperature") == STATE_UNAVAILABLE

    mock_unit.fail_read(13001, None, register_type="input")
    await config_entry.runtime_data.settings.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, "sensor", "total_pv_generation") == "5009.6"


async def test_a_tiny_dip_is_ignored_but_a_reset_is_not(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    assert state(hass, "sensor", "total_pv_generation") == "5009.6"  # 50096 in 0.1 kWh
    assert state(hass, "sensor", "daily_pv_generation") == "28.5"

    # A lifetime counter (state class total) dipping 0.3 %: a firmware quirk.
    mock_unit.input[13002], mock_unit.input[13003] = 50080, 0
    mock_unit.input[13001] = 284
    await runtime.settings.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, "sensor", "total_pv_generation") == "5009.6"
    assert state(hass, "sensor", "daily_pv_generation") == "28.5"

    # A daily counter going to 0 is midnight; a real increase is taken.
    mock_unit.input[13001] = 0
    mock_unit.input[13002], mock_unit.input[13003] = 50100, 0
    await runtime.settings.async_refresh()
    await hass.async_block_till_done()
    assert state(hass, "sensor", "daily_pv_generation") == "0.0"
    assert state(hass, "sensor", "total_pv_generation") == "5010.0"


async def test_state_enum_before_a_read_is_unknown_not_inconsistent(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """HA's own "unknown" state is reserved for "no value"."""
    await setup_entry(hass, config_entry)
    found = hass.states.get(entity_id(hass, "sensor", "battery_mode"))
    assert found is not None
    assert "inconsistent" in found.attributes["options"]
    assert STATE_UNKNOWN not in found.attributes["options"]
