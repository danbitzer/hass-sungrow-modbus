"""The raw control entities: number, select, switch, button."""

from __future__ import annotations

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from modbus_connection import IllegalDataValueError, ModbusTimeoutError
from modbus_connection.mock import MockModbusUnit, WriteEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sungrow.const import DOMAIN

from .conftest import SERIAL, setup_entry


@pytest.fixture
def writes(mock_unit: MockModbusUnit) -> list[WriteEvent]:
    events: list[WriteEvent] = []
    mock_unit.on_write(events.append)
    return events


def addresses(writes: list[WriteEvent]) -> list[tuple[int, int]]:
    return [(w.address, w.values[0]) for w in writes]


def eid(hass: HomeAssistant, platform: str, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SERIAL}_{key}")
    assert found is not None, key
    return found


def state(hass: HomeAssistant, entity_id: str) -> str:
    found = hass.states.get(entity_id)
    assert found is not None, entity_id
    return found.state


async def test_number_writes_and_shows_the_read_back(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
) -> None:
    await setup_entry(hass, config_entry)
    power = eid(hass, "number", "battery_forced_charge_discharge_power")
    assert state(hass, power) == "10000.0"  # the leftover in the capture
    found = hass.states.get(power)
    assert found is not None
    assert found.attributes["max"] == 15000  # min(nominal, BDC): what it can do
    assert found.attributes["step"] == 100

    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: power, "value": 3000}, blocking=True
    )
    assert addresses(writes) == [(13051, 3000)]
    assert state(hass, power) == "3000.0"  # published from the read-back
    assert mock_unit.holding[13051] == 3000

    mode = hass.states.get(eid(hass, "sensor", "battery_mode"))
    assert mode is not None and mode.attributes["forced_power_w"] == 3000


async def test_number_rejects_what_the_register_rejects(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    limit = eid(hass, "number", "battery_max_charge_power")
    with pytest.raises(ServiceValidationError, match="multiple of 10"):
        await hass.services.async_call(
            "number", "set_value", {ATTR_ENTITY_ID: limit, "value": 1005}, blocking=True
        )
    assert addresses(writes) == []


async def test_zero_active_power_ratio_is_refused_while_shutdown_at_zero_is_on(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    ratio = eid(hass, "number", "active_power_limit_ratio")
    assert state(hass, ratio) == "100.0"
    with pytest.raises(ServiceValidationError, match="shut the inverter down"):
        await hass.services.async_call(
            "number", "set_value", {ATTR_ENTITY_ID: ratio, "value": 0}, blocking=True
        )
    assert addresses(writes) == []


async def test_number_write_failures_are_typed(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    power = eid(hass, "number", "battery_forced_charge_discharge_power")
    mock_unit.fail_write(13051, IllegalDataValueError())
    with pytest.raises(HomeAssistantError, match="failed"):
        await hass.services.async_call(
            "number", "set_value", {ATTR_ENTITY_ID: power, "value": 2000}, blocking=True
        )
    mock_unit.fail_write(13051, ModbusTimeoutError())
    with pytest.raises(HomeAssistantError, match="may or may not"):
        await hass.services.async_call(
            "number", "set_value", {ATTR_ENTITY_ID: power, "value": 2000}, blocking=True
        )
    assert state(hass, power) == "10000.0"


async def test_select_writes_the_enum(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    ems = eid(hass, "select", "ems_mode")
    command = eid(hass, "select", "battery_forced_charge_discharge")
    assert state(hass, ems) == "self_consumption"
    assert state(hass, command) == "discharge"
    found = hass.states.get(ems)
    assert found is not None
    assert found.attributes["options"] == [
        "self_consumption",
        "compulsory",
        "external_ems",
        "vpp",
    ]

    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: command, "option": "charge"},
        blocking=True,
    )
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: ems, "option": "compulsory"},
        blocking=True,
    )
    assert addresses(writes) == [(13050, 0xAA), (13049, 2)]
    assert state(hass, ems) == "compulsory"
    assert state(hass, eid(hass, "sensor", "battery_mode")) == "forced_charge"


async def test_switch_writes_aa55(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    pv = eid(hass, "switch", "pv_power_limitation")
    export = eid(hass, "switch", "export_power_limit")
    assert state(hass, pv) == STATE_OFF  # PV allowed
    assert state(hass, export) == STATE_ON

    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: pv}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: export}, blocking=True
    )
    assert addresses(writes) == [(13017, 0xAA), (13086, 0x55)]
    assert state(hass, pv) == STATE_ON
    assert state(hass, export) == STATE_OFF
    mode = hass.states.get(eid(hass, "sensor", "battery_mode"))
    assert mode is not None
    assert mode.attributes["pv_limited"] is True
    assert mode.attributes["export_limit_enabled"] is False


async def test_start_button_writes_the_command(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    writes: list[WriteEvent],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_entry(hass, config_entry)
    start = eid(hass, "button", "start_inverter")
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: start}, blocking=True
    )
    assert addresses(writes) == [(12999, 0xCF)]
    assert "Start inverter requested" in caplog.text


async def test_stop_button_is_disabled_by_default_and_restores_first(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
) -> None:
    await setup_entry(hass, config_entry)
    registry = er.async_get(hass)
    stop = eid(hass, "button", "stop_inverter")
    entry = registry.async_get(stop)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    registry.async_update_entity(stop, disabled_by=None)
    await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(stop) is not None

    mock_unit.holding[13049] = 2  # a forced mode was left behind
    mock_unit.holding[13050] = 0xAA
    await config_entry.runtime_data.settings.async_refresh()
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: stop}, blocking=True
    )
    # self-consumption first (EMS mode, then the leftover command), then stop
    assert addresses(writes) == [(13049, 0), (12999, 0xCE)]
