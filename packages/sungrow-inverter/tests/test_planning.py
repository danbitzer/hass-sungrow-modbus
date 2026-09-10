"""Every field sits inside the declared ranges, and reads never cross them."""

from __future__ import annotations

import pytest
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
    Settings,
    StartPower,
)
from sungrow_inverter.const import MAX_SPAN

INPUT_COMPONENTS = (
    Identity,
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
    blocks = unit.read_events
    assert all(b.register_type == "input" for b in blocks)
    # ac_dc 3, flows 1, grid_phases 1, meter 2, backup 1, battery 2, battery_power 1
    assert len(blocks) == 11
    assert ReadEvent("input", 5010, 11) in blocks
    assert ReadEvent("input", 12999, 12) in blocks
    assert ReadEvent("input", 5213, 2) in blocks


async def test_settings_poll_costs_a_known_number_of_reads(
    inverter: SungrowInverter, unit: MockModbusUnit
) -> None:
    await inverter.async_update()
    unit.read_events.clear()
    await inverter.async_update_settings()
    blocks = unit.read_events
    holding = [b for b in blocks if b.register_type == "holding"]
    inputs = [b for b in blocks if b.register_type == "input"]
    # settings 6 (one per holding range it touches), battery_limits 1,
    # start_power 1, apl_shadow 1
    assert len(holding) == 9
    # energy 5 (5002-5004, 5007, 13001-13028, 13035-13041, 13044-13046), alarms 1
    assert len(inputs) == 6
    assert ReadEvent("input", 13049, 30) in inputs


async def test_two_mppt_model_never_reads_mppt3(unit: MockModbusUnit) -> None:
    unit.input[4999] = 0x0E23  # SH10T
    inverter = SungrowInverter(unit)
    await inverter.async_update()
    assert inverter.model is not None and inverter.model.mppt == 2
    assert inverter.ac_dc.mppt3_voltage is None
    assert inverter.ac_dc.mppt3_power is None
    assert inverter.ac_dc.mppt1_voltage == 380.0
    assert inverter.ac_dc.total_dc_power == 6250
    for event in unit.read_events:
        if event.register_type != "input":
            continue
        covered = range(event.address, event.address + event.count)
        assert 5014 not in covered and 5015 not in covered, event
