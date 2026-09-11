"""Write paths: aa55 round-trips through the validator, validators reject."""

from __future__ import annotations

import pytest
from modbus_connection.mock import MockModbusUnit, WriteEvent

from sungrow_inverter import ChargeCommand, EmsMode, StartStop
from sungrow_inverter.components import AplShadow, BatteryLimits, Control, Settings
from sungrow_inverter.fields import (
    multiple_of_10,
    non_negative_u16,
    percent,
    ratio,
    soc_lower,
    soc_upper,
)


@pytest.fixture
def writes(unit: MockModbusUnit) -> list[WriteEvent]:
    events: list[WriteEvent] = []
    unit.on_write(events.append)
    return events


async def test_aa55_write_round_trips(
    unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    settings = Settings(unit)
    await settings.async_update()
    assert settings.pv_power_limitation is False

    await settings.write("pv_power_limitation", True)
    assert writes[-1] == WriteEvent("holding", 13017, [0xAA], 0x06)
    await settings.async_update()
    assert settings.pv_power_limitation is True

    await settings.write("pv_power_limitation", False)
    assert writes[-1] == WriteEvent("holding", 13017, [0x55], 0x06)
    await settings.async_update()
    assert settings.pv_power_limitation is False


async def test_aa55_rejects_non_bool(unit: MockModbusUnit) -> None:
    settings = Settings(unit)
    with pytest.raises(ValueError, match="bool"):
        await settings.write("pv_power_limitation", 0xAA)


async def test_aa55_read_only_shadow_refuses_writes(unit: MockModbusUnit) -> None:
    apl = AplShadow(unit)
    with pytest.raises(AttributeError, match="read-only"):
        await apl.write("apl_shutdown_at_zero", False)


async def test_enum_writes_encode_the_code(
    unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    settings = Settings(unit)
    await settings.write("ems_mode", EmsMode.COMPULSORY)
    await settings.write("charge_command", ChargeCommand.CHARGE)
    assert writes == [
        WriteEvent("holding", 13049, [2], 0x06),
        WriteEvent("holding", 13050, [0xAA], 0x06),
    ]
    await settings.async_update()
    assert settings.ems_mode is EmsMode.COMPULSORY
    assert settings.charge_command is ChargeCommand.CHARGE


async def test_start_stop_writes_the_command_word(
    unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    control = Control(unit)
    await control.write("start_stop", StartStop.STOP)
    await control.write("start_stop", StartStop.START)
    assert [w.values for w in writes] == [[0xCE], [0xCF]]
    assert not unit.read_events  # never polled


async def test_battery_limit_writes_in_tens_of_watts(
    unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    limits = BatteryLimits(unit)
    await limits.write("max_charge_power", 10)
    await limits.write("max_discharge_power", 12000)
    assert [w.values for w in writes] == [[1], [1200]]
    await limits.async_update()
    assert limits.max_charge_power == 10
    assert limits.max_discharge_power == 12000


async def test_battery_limit_rejects_off_step(unit: MockModbusUnit) -> None:
    limits = BatteryLimits(unit)
    with pytest.raises(ValueError, match="multiple of 10"):
        await limits.write("max_charge_power", 12005)
    with pytest.raises(ValueError):
        await limits.write("max_charge_power", -10)


async def test_scaled_writes_encode_tenths(
    unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    settings = Settings(unit)
    await settings.write("max_soc", 95.5)
    await settings.write("min_soc", 5)
    await settings.write("feed_in_ratio", 42.7)
    await settings.write("forced_power", 2000)
    await settings.write("backup_reserve_soc", 30)
    assert [w.values for w in writes] == [[955], [50], [427], [2000], [30]]


@pytest.mark.parametrize(
    ("validator", "bad"),
    [
        (non_negative_u16, -1),
        (non_negative_u16, 65536),
        (non_negative_u16, 1.5),
        (soc_upper, 49.9),
        (soc_upper, 100.1),
        (soc_lower, -0.1),
        (soc_lower, 50.1),
        (ratio, 100.5),
        (ratio, -1),
        (percent, 101),
        (percent, 2.5),
        (multiple_of_10, 15),
        (multiple_of_10, 10 * 0xFFFF + 10),
    ],
)
def test_validators_reject(validator: object, bad: float) -> None:
    assert callable(validator)
    with pytest.raises(ValueError):
        validator(bad)


@pytest.mark.parametrize(
    ("validator", "good", "expected"),
    [
        (non_negative_u16, 0, 0),
        (non_negative_u16, 65535.0, 65535),
        (soc_upper, 50, 50.0),
        (soc_lower, 50, 50.0),
        (ratio, 0, 0.0),
        (percent, 100.0, 100),
        (multiple_of_10, 0, 0),
        (multiple_of_10, 12000.0, 12000),
    ],
)
def test_validators_accept(validator: object, good: float, expected: float) -> None:
    assert callable(validator)
    assert validator(good) == expected


async def test_input_components_are_read_only(unit: MockModbusUnit) -> None:
    from sungrow_inverter.components import Battery

    battery = Battery(unit)
    with pytest.raises(AttributeError):
        await battery.write("battery_level", 50.0)
