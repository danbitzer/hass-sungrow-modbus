"""Setup and teardown of a config entry."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from modbus_connection import ModbusTimeoutError
from modbus_connection.mock import MockModbusUnit
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sungrow.const import CONF_DEVICE_TYPE_CODE
from sungrow_inverter import RetryingUnit, RetryPolicy, UpdateReport

from .conftest import SERIAL, setup_entry


def entry_state(entry: MockConfigEntry) -> ConfigEntryState:
    state: ConfigEntryState = entry.state
    return state


async def test_setup_and_unload(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
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
    assert device.name == "Sungrow SH15T"  # explicit, not the entry title
    assert device.manufacturer == "Sungrow"
    assert device.model == "SH15T"
    assert device.sw_version == "PEARL-H_B000.V000.P063"
    assert device.hw_version is None  # the protocol version is not hardware

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert entry_state(config_entry) is ConfigEntryState.NOT_LOADED
    assert mock_unit.message_spacing == 0  # the shared connection is left clean


async def test_settings_poll_holds_the_control_lock(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """The invariant M5's write sequences rely on."""
    await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    lock = runtime.device.battery_control.lock
    seen: list[bool] = []
    original: Callable[[], Awaitable[UpdateReport]] = runtime.settings._poll

    async def spy() -> UpdateReport:
        seen.append(lock.locked())
        return await original()

    runtime.settings._poll = spy
    await runtime.settings.async_refresh()
    assert seen == [True]
    assert not lock.locked()

    seen.clear()
    runtime.realtime._poll = spy  # the measurement tier does not take it
    await runtime.realtime.async_refresh()
    assert seen == [False]


async def test_settings_poll_yields_to_a_control_call(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    """A refresh while a control call holds the lock returns the last
    report rather than waiting on itself."""
    await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    before = runtime.settings.data
    async with runtime.device.battery_control.lock:
        await asyncio.wait_for(runtime.settings.async_refresh(), timeout=1)
    assert runtime.settings.data is before
    assert runtime.settings.last_update_success


async def test_snapshot_publishes_now(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    runtime.settings.data.failed["settings"] = ModbusTimeoutError()
    runtime.settings.data.updated.remove("settings")
    then = runtime.settings.data.at
    runtime.settings.async_apply_snapshot("settings")
    now = runtime.settings.data
    assert "settings" in now.updated and "settings" not in now.failed
    assert then < now.at <= time.monotonic()


async def test_three_silent_polls_drop_the_link(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    fast = RetryPolicy(attempts=1)
    with patch(
        "custom_components.sungrow.RetryingUnit",
        side_effect=lambda unit: RetryingUnit(unit, fast),
    ):
        await setup_entry(hass, config_entry)
    runtime = config_entry.runtime_data
    mock_unit.fail_requests(ModbusTimeoutError())
    with patch.object(mock_unit, "disconnect", AsyncMock()) as disconnect:
        for _ in range(2):
            await runtime.realtime.async_refresh()
        assert disconnect.await_count == 0
        await runtime.realtime.async_refresh()
        assert disconnect.await_count == 1
        await hass.async_block_till_done()
        battery = hass.states.get("sensor.sungrow_sh15t_battery_level")
        total = hass.states.get("sensor.sungrow_sh15t_total_pv_generation")
        assert battery is not None and battery.state == STATE_UNAVAILABLE
        assert total is not None and total.state == "5009.6"

        mock_unit.fail_requests(None)
        await runtime.realtime.async_refresh()
        await hass.async_block_till_done()
        assert disconnect.await_count == 1
    battery = hass.states.get("sensor.sungrow_sh15t_battery_level")
    assert battery is not None and battery.state == "97.8"


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
