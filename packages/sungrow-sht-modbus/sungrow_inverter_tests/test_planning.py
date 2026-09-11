"""Every field sits inside the declared ranges, and reads never cross them."""

from __future__ import annotations

import pytest
from modbus_connection import IllegalDataAddressError
from modbus_connection.mock import MockModbusUnit, ReadEvent
from modbus_connection.model import Component, RegisterField

from sungrow_inverter import HOLDING_RANGES, INPUT_RANGES, SungrowInverter
from sungrow_inverter.components import (
    AcDc,
    Alarms,
    AplShadow,
    Backup,
    Battery,
    BatteryLimits,
    BatteryPower,
    Energy,
    FirmwareInfo,
    Flows,
    GridPhases,
    Identity,
    Meter,
    Ratings,
    Settings,
    StartPower,
)
from sungrow_inverter.const import MAX_SPAN

from .conftest import SERIAL

INPUT_COMPONENTS = (
    Identity,
    Ratings,
    FirmwareInfo,
    AcDc,
    Flows,
    GridPhases,
    Meter,
    Backup,
    Battery,
    BatteryPower,
    Energy,
    Alarms,
)
HOLDING_COMPONENTS = (Settings, BatteryLimits, StartPower, AplShadow)


def _inside(address: int, count: int, ranges: tuple[tuple[int, int], ...]) -> bool:
    return any(low <= address and address + count - 1 <= high for low, high in ranges)


@pytest.mark.parametrize("component", INPUT_COMPONENTS + HOLDING_COMPONENTS)
def test_every_field_lies_within_one_declared_range(
    component: type[Component],
) -> None:
    ranges = INPUT_RANGES if component in INPUT_COMPONENTS else HOLDING_RANGES
    for name, field in component.declared_fields.items():
        assert isinstance(field, RegisterField)
        assert _inside(field.address, field.count, ranges), (
            f"{component.__name__}.{name} at {field.address} is outside the ranges"
        )


def test_ranges_are_sorted_and_disjoint() -> None:
    for ranges in (INPUT_RANGES, HOLDING_RANGES):
        for (low, high), (next_low, _) in zip(ranges, ranges[1:], strict=False):
            assert low <= high
            assert high < next_low


def test_no_register_is_claimed_by_two_fields_of_one_component() -> None:
    for component in INPUT_COMPONENTS + HOLDING_COMPONENTS:
        claimed: dict[int, str] = {}
        for name, field in component.declared_fields.items():
            assert isinstance(field, RegisterField)
            for address in range(field.address, field.address + field.count):
                if name.startswith("running_state"):
                    continue  # raw and decoded views of the same word
                assert address not in claimed, (
                    f"{component.__name__}: {name} overlaps {claimed[address]}"
                )
                claimed[address] = name


async def test_reads_stay_inside_ranges_and_under_max_span(
    inverter: SungrowInverter, unit: MockModbusUnit
) -> None:
    await inverter.async_update()
    for event in unit.read_events:
        ranges = INPUT_RANGES if event.register_type == "input" else HOLDING_RANGES
        assert event.count <= MAX_SPAN
        assert _inside(event.address, event.count, ranges), event


async def test_realtime_poll_costs_a_known_number_of_reads(
    inverter: SungrowInverter, unit: MockModbusUnit
) -> None:
    await inverter.async_update()  # setup + first poll
    unit.read_events.clear()
    await inverter.async_update_realtime()
    # One entry per component, in poll order; a new field that silently
    # widens a neighbour's block shows up here.
    assert unit.read_events == [
        ReadEvent("input", 5010, 25),  # ac_dc
        ReadEvent("input", 5241, 1),
        ReadEvent("input", 12999, 12),  # flows
        ReadEvent("input", 13030, 5),  # grid_phases
        ReadEvent("input", 5600, 8),  # meter
        ReadEvent("input", 5722, 12),  # backup
        ReadEvent("input", 5630, 1),  # battery
        ReadEvent("input", 13019, 6),
        ReadEvent("input", 5213, 2),  # battery_power
    ]


async def test_settings_poll_costs_a_known_number_of_reads(
    inverter: SungrowInverter, unit: MockModbusUnit
) -> None:
    await inverter.async_update()
    unit.read_events.clear()
    await inverter.async_update_settings()
    assert unit.read_events == [
        ReadEvent("holding", 13017, 83),  # settings, one frame
        ReadEvent("holding", 33046, 2),  # battery_limits
        ReadEvent("input", 5002, 6),  # energy
        ReadEvent("input", 13001, 46),
        ReadEvent("holding", 33148, 2),  # start_power
        ReadEvent("holding", 31212, 1),  # apl_shadow
        ReadEvent("input", 13049, 30),  # alarms
    ]


async def test_two_mppt_model_never_reads_mppt3(unit: MockModbusUnit) -> None:
    unit.input[4999] = 0x0E23  # SH10T
    inverter = SungrowInverter(unit)
    await inverter.async_update()
    assert inverter.model is not None and inverter.model.mppt == 2
    assert inverter.ac_dc.mppt3_voltage is None
    assert inverter.ac_dc.mppt3_power is None
    assert inverter.ac_dc.mppt1_voltage == 380.0
    assert inverter.ac_dc.total_dc_power == 6250
    raw = await inverter.async_read_raw()
    for event in unit.read_events:
        if event.register_type != "input":
            continue
        covered = range(event.address, event.address + event.count)
        assert 5014 not in covered and 5015 not in covered, event
    assert 5014 not in raw["input"] and 5015 not in raw["input"]
    assert 5013 in raw["input"] and 5016 in raw["input"]


async def test_setup_costs_a_known_number_of_reads(
    inverter: SungrowInverter, unit: MockModbusUnit
) -> None:
    await inverter.async_update_realtime()
    assert unit.read_events[:-9] == [
        ReadEvent("input", 4951, 51),  # identity
        ReadEvent("input", 5621, 18),  # ratings
        ReadEvent("input", 13249, 45),  # firmware
        ReadEvent("holding", 33148, 2),  # optional probes
        ReadEvent("holding", 31212, 1),
        ReadEvent("input", 13049, 30),
    ]


async def test_a_refused_merged_block_falls_back_to_the_narrow_map(
    unit: MockModbusUnit,
) -> None:
    """A link that refuses a read spanning a hole still works, per component."""
    unit.fail_read(4985, IllegalDataAddressError(), register_type="input")  # identity
    unit.fail_read(13029, IllegalDataAddressError(), register_type="input")  # energy
    unit.fail_read(13060, IllegalDataAddressError())  # settings
    inverter = SungrowInverter(unit)
    report = await inverter.async_update()
    assert report.ok
    assert inverter.identity.serial == SERIAL
    assert inverter.energy.total_export == 6000.0
    assert inverter.settings.backup_reserve_soc == 20
    events = unit.read_events
    assert ReadEvent("input", 4951, 51) in events  # tried wide first
    assert ReadEvent("input", 4951, 32) in events  # then narrow
    assert ReadEvent("input", 4989, 13) in events
    assert ReadEvent("holding", 13017, 83) in events
    assert ReadEvent("holding", 13049, 3) in events
    assert ReadEvent("holding", 13099, 1) in events
    # the fallback sticks: the next poll goes straight to the narrow blocks
    unit.read_events.clear()
    await inverter.async_update_settings()
    assert ReadEvent("holding", 13017, 83) not in unit.read_events
    assert ReadEvent("holding", 13017, 1) in unit.read_events
    # other components are untouched
    assert ReadEvent("input", 13049, 30) in unit.read_events


async def test_fallback_keeps_the_mppt_restriction(unit: MockModbusUnit) -> None:
    unit.input[4999] = 0x0E23  # SH10T, two MPPT
    unit.fail_read(5025, IllegalDataAddressError(), register_type="input")
    inverter = SungrowInverter(unit)
    assert (await inverter.async_update()).ok
    assert inverter.ac_dc.mppt3_voltage is None
    assert inverter.ac_dc.mppt1_voltage == 380.0
    assert inverter.ac_dc.grid_frequency == 50.01


async def test_a_refusal_on_the_narrow_map_is_real(unit: MockModbusUnit) -> None:
    unit.fail_read(13001, IllegalDataAddressError(), register_type="input")
    inverter = SungrowInverter(unit)
    report = await inverter.async_update()
    assert isinstance(report.failed["energy"], IllegalDataAddressError)
