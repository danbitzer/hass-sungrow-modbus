"""Sensors and binary sensors against the live capture."""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from modbus_connection import ServerDeviceFailureError
from modbus_connection.mock import MockModbusUnit
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sungrow.const import DOMAIN

from .conftest import ENTRY_DATA, ENTRY_OPTIONS, SERIAL, setup_entry


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
    assert state(hass, "sensor", "export_power_limit") == "15000"
    assert state(hass, "sensor", "battery_max_charge_power") == "10000"
    assert state(hass, "sensor", "battery_charging_start_power") == "unknown"  # 0xFFFF

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
    expected = {
        "battery_level": ("%", "battery", "measurement"),
        "battery_power": ("W", "power", "measurement"),
        "load_power": ("W", "power", "measurement"),
        "total_pv_generation": ("kWh", "energy", "total"),
        "daily_pv_generation": ("kWh", "energy", "total_increasing"),
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
