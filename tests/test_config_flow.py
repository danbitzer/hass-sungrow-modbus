"""The config flow: probe, gate on the model, unique id, options, reconfigure."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from modbus_connection import ModbusTimeoutError
from modbus_connection.mock import MockModbusUnit
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sungrow.const import (
    CONF_BATTERY_MAX_POWER_W,
    CONF_BDC_RATED_POWER_W,
    CONF_DEVICE_TYPE_CODE,
    CONF_MESSAGE_SPACING_MS,
    CONF_NOMINAL_POWER_W,
    CONF_REALTIME_INTERVAL,
    CONF_SERIAL,
    CONF_SETTINGS_INTERVAL,
    DOMAIN,
)

from .conftest import CONNECTION, SERIAL, setup_entry


async def test_user_flow_creates_the_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONNECTION
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sungrow SH15T"
    entry = result["result"]
    assert entry.unique_id == SERIAL
    assert entry.data[CONF_SERIAL] == SERIAL
    assert entry.data[CONF_DEVICE_TYPE_CODE] == 0x0E25
    assert entry.data[CONF_NOMINAL_POWER_W] == 15000
    assert entry.data[CONF_BDC_RATED_POWER_W] == 30000
    # the restore target defaults to the lower rating, never the 30 kW BDC
    assert entry.options[CONF_BATTERY_MAX_POWER_W] == 15000


async def test_cannot_connect_then_recovers(
    hass: HomeAssistant, mock_unit: MockModbusUnit
) -> None:
    mock_unit.fail_requests(ModbusTimeoutError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONNECTION
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    mock_unit.fail_requests(None)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONNECTION
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_non_sht_inverter_is_named_in_the_error(
    hass: HomeAssistant, mock_unit: MockModbusUnit
) -> None:
    mock_unit.input[4999] = 0x0E03  # SH10RT
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], CONNECTION
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "unsupported_model"}
    assert result["description_placeholders"] == {"model": "SH10RT", "code": "0x0E03"}


async def test_same_inverter_twice_aborts(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONNECTION, CONF_HOST: "192.168.1.50"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_options_flow(hass: HomeAssistant, config_entry: MockConfigEntry) -> None:
    await setup_entry(hass, config_entry)
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_REALTIME_INTERVAL: 15,
            CONF_SETTINGS_INTERVAL: 120,
            CONF_BATTERY_MAX_POWER_W: 12000,
            CONF_MESSAGE_SPACING_MS: 100,
            "include_register_dump": False,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options[CONF_BATTERY_MAX_POWER_W] == 12000
    assert config_entry.options[CONF_REALTIME_INTERVAL] == 15
    # the entry reloaded with the new restore target
    assert config_entry.runtime_data.device.battery_max_power_w == 12000


async def test_reconfigure_requires_the_same_inverter(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    from modbus_connection.encode import encode_string

    mock_unit.input[4989] = encode_string("A987654321", length=10)  # another unit
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONNECTION, CONF_HOST: "192.168.1.50"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_device"

    mock_unit.input[4989] = encode_string(SERIAL, length=10)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**CONNECTION, CONF_HOST: "192.168.1.50"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
