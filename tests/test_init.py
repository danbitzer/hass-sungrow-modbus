"""Setup and teardown of a config entry."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from modbus_connection import ModbusTimeoutError
from modbus_connection.mock import MockModbusUnit
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sungrow.const import CONF_DEVICE_TYPE_CODE

from .conftest import SERIAL, setup_entry


def entry_state(entry: MockConfigEntry) -> ConfigEntryState:
    state: ConfigEntryState = entry.state
    return state


async def test_setup_and_unload(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    assert entry_state(config_entry) is ConfigEntryState.LOADED
    runtime = config_entry.runtime_data
    assert runtime.device.model is not None and runtime.device.model.name == "SH15T"
    assert runtime.realtime.data is not None and runtime.realtime.data.ok
    assert runtime.settings.data is not None and runtime.settings.data.ok
    assert runtime.device.battery_max_power_w == 10000  # the option, not the rating
    assert runtime.device.modbus_unit.wrapped.message_spacing == 0.05

    device = dr.async_get(hass).async_get_device_by_identifier(
        ("sungrow", SERIAL), config_entry.entry_id
    )
    assert device is not None
    assert device.manufacturer == "Sungrow"
    assert device.model == "SH15T"
    assert device.sw_version == "PEARL-H_B000.V000.P063"

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert entry_state(config_entry) is ConfigEntryState.NOT_LOADED


async def test_unreachable_inverter_retries_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    mock_unit.fail_requests(ModbusTimeoutError())
    config_entry.add_to_hass(hass)
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_link_in_use_elsewhere_retries_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    with patch(
        "custom_components.sungrow.async_get_unit",
        side_effect=HomeAssistantError("in use with different link settings"),
    ):
        config_entry.add_to_hass(hass)
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_wrong_model_is_a_setup_error(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    mock_unit.input[4999] = 0x0E03
    config_entry.add_to_hass(hass)
    assert config_entry.data[CONF_DEVICE_TYPE_CODE] == 0x0E25
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
