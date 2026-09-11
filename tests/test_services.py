"""The actions: guarded sequences end to end, responses, typed errors."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.const import ATTR_DEVICE_ID, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from modbus_connection import (
    IllegalDataAddressError,
    IllegalDataValueError,
    ModbusTimeoutError,
    ServerDeviceFailureError,
)
from modbus_connection.mock import MockModbusUnit, WriteEvent
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sungrow.const import DOMAIN

from .conftest import SERIAL, setup_entry

FENCE = 1  # 10 W in 0.01 kW counts


@pytest.fixture
def writes(mock_unit: MockModbusUnit) -> list[WriteEvent]:
    events: list[WriteEvent] = []
    mock_unit.on_write(events.append)
    return events


def addresses(writes: list[WriteEvent]) -> list[tuple[int, int]]:
    return [(w.address, w.values[0]) for w in writes]


def device_id(hass: HomeAssistant, config_entry: MockConfigEntry) -> str:
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, SERIAL), config_entry.entry_id
    )
    assert device is not None
    return device.id


def entity(hass: HomeAssistant, platform: str, key: str) -> str:
    found = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{SERIAL}_{key}")
    assert found is not None, key
    return found


def battery_mode(hass: HomeAssistant) -> str:
    state = hass.states.get(entity(hass, "sensor", "battery_mode"))
    assert state is not None
    return state.state


def mode_attr(hass: HomeAssistant, name: str) -> object:
    state = hass.states.get(entity(hass, "sensor", "battery_mode"))
    assert state is not None
    return state.attributes[name]


def holding_reads(mock_unit: MockModbusUnit) -> list[int]:
    return [e.address for e in mock_unit.read_events if e.register_type == "holding"]


async def call(hass: HomeAssistant, service: str, data: dict) -> dict:  # type: ignore[type-arg]
    result = await hass.services.async_call(
        DOMAIN, service, data, blocking=True, return_response=True
    )
    assert isinstance(result, dict)
    return result


async def test_hold_then_repeat_is_write_free(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    writes: list[WriteEvent],
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    assert battery_mode(hass) == "self_consumption"

    result = await call(
        hass, "set_battery_mode", {ATTR_DEVICE_ID: device, "mode": "hold"}
    )
    assert addresses(writes) == [(33046, FENCE), (33047, FENCE)]
    assert result["verified"] is True
    assert result["battery_mode"] == "hold"
    assert [w["field"] for w in result["writes"]] == [
        "max_charge_power",
        "max_discharge_power",
    ]
    assert "settings.ems_mode" in result["skipped"]
    assert battery_mode(hass) == "hold"  # published at once, no poll

    writes.clear()
    result = await call(
        hass, "set_battery_mode", {ATTR_DEVICE_ID: device, "mode": "hold"}
    )
    assert addresses(writes) == []
    assert result["writes"] == [] and result["verified"] is False
    assert battery_mode(hass) == "hold"


async def test_forced_charge_then_back(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    result = await call(
        hass,
        "set_battery_mode",
        {ATTR_DEVICE_ID: device, "mode": "forced_charge", "power_w": 2000},
    )
    assert addresses(writes) == [(13051, 2000), (13050, 0xAA), (13049, 2)]
    assert result["battery_mode"] == "forced_charge"
    assert battery_mode(hass) == "forced_charge"

    writes.clear()
    await call(
        hass, "set_battery_mode", {ATTR_DEVICE_ID: device, "mode": "self_consumption"}
    )
    assert addresses(writes) == [(13049, 0)]  # EMS first; limits already restored
    assert battery_mode(hass) == "self_consumption"


async def test_battery_mode_validation(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    with pytest.raises(ServiceValidationError, match="needs a power"):
        await call(
            hass, "set_battery_mode", {ATTR_DEVICE_ID: device, "mode": "forced_charge"}
        )
    with pytest.raises(ServiceValidationError, match="outside"):
        await call(
            hass,
            "set_battery_mode",
            {ATTR_DEVICE_ID: device, "mode": "forced_discharge", "power_w": 12000},
        )
    with pytest.raises(ServiceValidationError):
        await call(hass, "set_battery_mode", {ATTR_DEVICE_ID: "nope", "mode": "hold"})
    with pytest.raises(Exception, match="must be one of"):  # noqa: B017
        await call(hass, "set_battery_mode", {ATTR_DEVICE_ID: device, "mode": "vpp"})
    assert addresses(writes) == []


async def test_verification_failure_is_reported_and_state_shown(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)

    def refuse_silently(event: WriteEvent) -> None:
        if event.address == 13049:
            mock_unit.holding[13049] = 0  # the inverter ignores the write

    mock_unit.on_write(refuse_silently)
    with pytest.raises(HomeAssistantError, match="settings.ems_mode reads"):
        await call(
            hass,
            "set_battery_mode",
            {ATTR_DEVICE_ID: device, "mode": "forced_discharge", "power_w": 1000},
        )
    # the command already read "discharge"; the power landed, the mode did not
    assert addresses(writes) == [(13051, 1000), (13049, 2)]
    # the sensor shows what the inverter holds after the partial sequence
    found = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{SERIAL}_battery_mode"
    )
    assert found is not None
    state = hass.states.get(found)
    assert state is not None
    assert state.attributes["forced_power_w"] == 1000
    assert state.attributes["ems_mode"] == "self_consumption"


async def test_unverified_call_polls_instead(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    result = await call(
        hass,
        "set_battery_mode",
        {ATTR_DEVICE_ID: device, "mode": "no_charge", "verify": False},
    )
    assert addresses(writes) == [(33046, FENCE)]
    assert result["verified"] is False
    assert battery_mode(hass) == "no_charge"  # a full settings poll followed


async def test_export_limit(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    result = await call(
        hass, "set_export_limit", {ATTR_DEVICE_ID: device, "limit_w": 0}
    )
    assert addresses(writes) == [(13073, 0)]  # already enabled in the capture
    assert "settings.export_limit_enabled" in result["skipped"]

    writes.clear()
    await call(hass, "set_export_limit", {ATTR_DEVICE_ID: device})
    assert addresses(writes) == [(13086, 0x55)]  # lifted; the value stays

    writes.clear()
    await call(
        hass,
        "set_export_limit",
        {ATTR_DEVICE_ID: device, "limit_w": 15000, "enabled": False},
    )
    assert addresses(writes) == [(13073, 15000)]


async def test_pv_limitation(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    await call(hass, "set_pv_limitation", {ATTR_DEVICE_ID: device, "limit": True})
    assert addresses(writes) == [(13017, 0xAA)]
    writes.clear()
    await call(hass, "set_pv_limitation", {ATTR_DEVICE_ID: device, "limit": True})
    assert addresses(writes) == []


async def test_stop_restores_self_consumption_first(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    await call(
        hass,
        "set_battery_mode",
        {ATTR_DEVICE_ID: device, "mode": "forced_charge", "power_w": 2000},
    )
    writes.clear()
    result = await call(hass, "stop_inverter", {ATTR_DEVICE_ID: device})
    assert addresses(writes) == [(13049, 0), (12999, 0xCE)]
    assert [w["field"] for w in result["writes"]] == ["ems_mode", "start_stop"]
    assert "Stop inverter requested" in caplog.text

    writes.clear()
    await call(hass, "start_inverter", {ATTR_DEVICE_ID: device})
    assert addresses(writes) == [(12999, 0xCF)]


async def test_unanswered_write_is_reported_honestly_and_replanned(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
) -> None:
    """The power landed, the command did not: HA shows the power the
    inverter holds (re-read, not the cache) and the next call re-reads
    before planning, so it does not skip the writes still needed."""
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    mock_unit.fail_write(13050, ModbusTimeoutError())
    with pytest.raises(HomeAssistantError, match="may or may not"):
        await call(
            hass,
            "set_battery_mode",
            {ATTR_DEVICE_ID: device, "mode": "forced_charge", "power_w": 2000},
        )
    assert addresses(writes) == [(13051, 2000)]
    assert battery_mode(hass) == "self_consumption"  # true: EMS never changed
    assert mode_attr(hass, "forced_power_w") == 2000  # re-read, not the cache

    mock_unit.fail_write(13050, None)
    mock_unit.read_events.clear()
    writes.clear()
    await call(
        hass,
        "set_battery_mode",
        {ATTR_DEVICE_ID: device, "mode": "forced_charge", "power_w": 2000},
    )
    # the re-read after the failure is trusted: the power is not rewritten,
    # the two registers that never landed are
    assert addresses(writes) == [(13050, 0xAA), (13049, 2)]
    assert battery_mode(hass) == "forced_charge"


async def test_failed_read_back_takes_the_entities_unavailable(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
) -> None:
    """All writes landed but the read-back was refused: nothing is claimed."""
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)

    def refuse_reads_after_the_last_write(event: WriteEvent) -> None:
        if event.address == 13049:
            mock_unit.fail_read(
                13017, IllegalDataAddressError(), register_type="holding"
            )

    mock_unit.on_write(refuse_reads_after_the_last_write)
    with pytest.raises(HomeAssistantError, match="read-back failed"):
        await call(
            hass,
            "set_battery_mode",
            {ATTR_DEVICE_ID: device, "mode": "forced_charge", "power_w": 2000},
        )
    assert addresses(writes) == [(13051, 2000), (13050, 0xAA), (13049, 2)]
    assert battery_mode(hass) == STATE_UNAVAILABLE
    number = hass.states.get(entity(hass, "number", "battery_max_charge_power"))
    assert number is not None and number.state == "10000.0"  # its block read fine


async def test_unverified_call_rereads_only_what_it_touched(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    mock_unit.read_events.clear()
    await call(
        hass,
        "set_pv_limitation",
        {ATTR_DEVICE_ID: device, "limit": True, "verify": False},
    )
    assert holding_reads(mock_unit) == [13017]  # settings only, no full poll
    assert mode_attr(hass, "pv_limited") is True


async def test_a_fresh_snapshot_is_trusted_and_a_stale_one_is_not(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    mock_unit.read_events.clear()
    await call(hass, "set_battery_mode", {ATTR_DEVICE_ID: device, "mode": "hold"})
    assert sorted(holding_reads(mock_unit)) == [13017, 33046]  # the read-back only

    runtime = config_entry.runtime_data
    runtime.device._refreshed["settings"] -= 120  # older than interval + margin
    runtime.device._refreshed["battery_limits"] -= 120
    mock_unit.read_events.clear()
    await call(
        hass, "set_battery_mode", {ATTR_DEVICE_ID: device, "mode": "self_consumption"}
    )
    reads = holding_reads(mock_unit)
    assert len(reads) == 4  # a pre-read of both, then the read-back of both
    assert battery_mode(hass) == "self_consumption"


async def test_start_and_stop_do_not_poll_the_settings_tier(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    mock_unit.read_events.clear()
    result = await call(hass, "start_inverter", {ATTR_DEVICE_ID: device})
    assert result["writes"][0]["field"] == "start_stop"
    assert holding_reads(mock_unit) == []
    assert mock_unit.read_events  # the measurement poll ran


async def test_stop_is_not_attempted_when_the_restore_fails(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    await call(
        hass,
        "set_battery_mode",
        {ATTR_DEVICE_ID: device, "mode": "forced_charge", "power_w": 2000},
    )
    writes.clear()
    mock_unit.fail_write(13049, IllegalDataValueError())
    with pytest.raises(HomeAssistantError, match="Writing settings.ems_mode failed"):
        await call(hass, "stop_inverter", {ATTR_DEVICE_ID: device})
    assert (12999, 0xCE) not in addresses(writes)
    assert battery_mode(hass) == "forced_charge"


async def test_export_limit_out_of_range_is_refused(
    hass: HomeAssistant, config_entry: MockConfigEntry, writes: list[WriteEvent]
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    with pytest.raises(ServiceValidationError, match="outside"):
        await call(hass, "set_export_limit", {ATTR_DEVICE_ID: device, "limit_w": 20000})
    assert addresses(writes) == []


async def test_no_response_when_not_asked(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    result = await hass.services.async_call(
        DOMAIN,
        "set_battery_mode",
        {ATTR_DEVICE_ID: device, "mode": "hold"},
        blocking=True,
    )
    assert result is None
    assert battery_mode(hass) == "hold"


async def test_an_action_after_a_failed_poll_revives_only_what_it_read(
    hass: HomeAssistant, config_entry: MockConfigEntry, mock_unit: MockModbusUnit
) -> None:
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    runtime = config_entry.runtime_data
    mock_unit.fail_requests(ModbusTimeoutError())
    await runtime.settings.async_refresh()
    await hass.async_block_till_done()
    mock_unit.fail_requests(None)
    assert not runtime.settings.last_update_success
    alarm = entity(hass, "binary_sensor", "inverter_alarm_active")
    assert battery_mode(hass) == STATE_UNAVAILABLE
    assert hass.states.get(alarm).state == STATE_UNAVAILABLE  # type: ignore[union-attr]

    await call(hass, "set_battery_mode", {ATTR_DEVICE_ID: device, "mode": "hold"})
    assert battery_mode(hass) == "hold"
    assert hass.states.get(alarm).state == STATE_UNAVAILABLE  # type: ignore[union-attr]
    total = hass.states.get(entity(hass, "sensor", "total_pv_generation"))
    assert total is not None and total.state == "5009.6"


async def test_a_failed_read_back_is_retried_by_replanning(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_unit: MockModbusUnit,
    writes: list[WriteEvent],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Live: every write landed, then the settings read-back got exception
    4 three times. The touched blocks are re-read and the plan re-run; with
    nothing left to write, the call counts as verified."""
    await setup_entry(hass, config_entry)
    device = device_id(hass, config_entry)
    original = mock_unit.read_holding_registers
    refusals = {"left": 0}

    async def flaky(address: int, count: int = 1, **kwargs: object) -> object:
        if address == 13017 and refusals["left"] > 0:
            refusals["left"] -= 1
            raise ServerDeviceFailureError()  # exception 4: contention
        return await original(address, count, **kwargs)

    def arm(event: WriteEvent) -> None:
        if event.address == 13049:
            refusals["left"] = 4  # outlasts the unit's retries, not the re-read

    mock_unit.on_write(arm)
    with patch.object(mock_unit, "read_holding_registers", flaky):
        result = await call(
            hass,
            "set_battery_mode",
            {ATTR_DEVICE_ID: device, "mode": "forced_charge", "power_w": 2000},
        )
    assert addresses(writes) == [(13051, 2000), (13050, 0xAA), (13049, 2)]
    assert [w["field"] for w in result["writes"]] == [
        "forced_power",
        "charge_command",
        "ems_mode",
    ]
    assert result["verified"] is True
    assert result["battery_mode"] == "forced_charge"
    assert battery_mode(hass) == "forced_charge"
    assert "planning again" in caplog.text
