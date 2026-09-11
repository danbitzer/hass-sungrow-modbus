"""The actions: guarded sequences end to end, responses, typed errors."""

from __future__ import annotations

import pytest
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er
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


def battery_mode(hass: HomeAssistant) -> str:
    found = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{SERIAL}_battery_mode"
    )
    assert found is not None
    state = hass.states.get(found)
    assert state is not None
    return state.state


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
