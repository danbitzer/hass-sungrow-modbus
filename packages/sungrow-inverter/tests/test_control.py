"""The guarded write layer against the mock: plan, guard, order, verify."""

from __future__ import annotations

import asyncio

import pytest
from modbus_connection import IllegalDataValueError, ServerDeviceFailureError
from modbus_connection.mock import MockModbusUnit, WriteEvent

from sungrow_inverter import (
    BatteryControl,
    BatteryMode,
    ChargeCommand,
    DesiredState,
    EmsMode,
    PowerOutOfRangeError,
    SettingsUnavailableError,
    SungrowInverter,
    VerificationError,
    WriteRejectedError,
)
from sungrow_inverter.control import same_on_wire


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
async def inv(unit: MockModbusUnit, sleeps: Sleeps) -> SungrowInverter:
    """A set-up inverter with 12 kW as the configured max power; limits at 12 kW."""
    inverter = SungrowInverter(unit, battery_max_power_w=12000)
    inverter.battery_control = BatteryControl(inverter, sleep=sleeps)
    await inverter.async_update()
    unit.read_events.clear()
    return inverter


@pytest.fixture
def writes(unit: MockModbusUnit) -> list[WriteEvent]:
    events: list[WriteEvent] = []
    unit.on_write(events.append)
    return events


def addresses(writes: list[WriteEvent]) -> list[tuple[int, int]]:
    return [(w.address, w.values[0]) for w in writes]


def mode(inv: SungrowInverter) -> BatteryMode | None:
    return inv.effective_battery_mode


# -- guard -------------------------------------------------------------------


async def test_self_consumption_when_already_there_writes_nothing(
    inv: SungrowInverter, writes: list[WriteEvent]
) -> None:
    report = await inv.battery_control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION))
    assert writes == []
    assert report.writes == [] and report.verified is False
    assert report.skipped == [
        "battery_limits.max_charge_power",
        "battery_limits.max_discharge_power",
        "settings.ems_mode",
    ]
    assert report.as_dict() == {
        "action": "self_consumption",
        "writes": [],
        "skipped": report.skipped,
        "verified": False,
    }


def test_guard_compares_at_register_resolution(inv: SungrowInverter) -> None:
    limits = inv.battery_limits
    assert same_on_wire(limits, "max_charge_power", 12000, 12000.0)
    assert not same_on_wire(limits, "max_charge_power", 12000, 12004)  # rejected
    assert not same_on_wire(limits, "max_charge_power", 12000, 10)
    assert not same_on_wire(limits, "max_charge_power", None, 12000)
    settings = inv.settings
    assert same_on_wire(settings, "pv_power_limitation", False, False)
    assert not same_on_wire(settings, "pv_power_limitation", False, True)
    assert same_on_wire(settings, "ems_mode", EmsMode.COMPULSORY, 2)


# -- plans and ordering --------------------------------------------------------


async def test_no_charge_fences_charging_only(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    report = await inv.battery_control.apply(DesiredState(BatteryMode.NO_CHARGE))
    assert addresses(writes) == [(33046, 1)]  # 10 W, raw 1
    assert report.verified is True
    assert report.skipped == ["battery_limits.max_discharge_power", "settings.ems_mode"]
    assert [w.as_dict() for w in report.writes] == [
        {
            "component": "battery_limits",
            "field": "max_charge_power",
            "previous": 12000,
            "value": 10,
        }
    ]
    assert mode(inv) is BatteryMode.NO_CHARGE


async def test_hold_fences_both_and_self_consumption_restores(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    await inv.battery_control.apply(DesiredState(BatteryMode.HOLD))
    assert addresses(writes) == [(33046, 1), (33047, 1)]
    assert mode(inv) is BatteryMode.HOLD
    writes.clear()
    report = await inv.battery_control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION))
    assert addresses(writes) == [(33046, 1200), (33047, 1200)]
    assert report.verified
    assert mode(inv) is BatteryMode.SELF_CONSUMPTION


async def test_forced_charge_writes_power_before_command_before_mode(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    unit.holding[33046] = 1  # charging was fenced by an earlier no_charge
    await inv.async_refresh("battery_limits")
    report = await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_CHARGE, power_w=2000)
    )
    assert addresses(writes) == [
        (33046, 1200),  # restore the fence first
        (13051, 2000),  # power
        (13050, 0xAA),  # command
        (13049, 2),  # mode last
    ]
    assert report.verified
    assert report.skipped == ["battery_limits.max_discharge_power"]
    assert mode(inv) is BatteryMode.FORCED_CHARGE
    writes.clear()
    # re-asserting the same state is write-free
    again = await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_CHARGE, power_w=2000)
    )
    assert writes == [] and again.writes == []


async def test_forced_discharge_then_back_to_self_consumption(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_DISCHARGE, power_w=1500)
    )
    assert addresses(writes) == [(13051, 1500), (13050, 0xBB), (13049, 2)]
    assert mode(inv) is BatteryMode.FORCED_DISCHARGE
    writes.clear()
    await inv.battery_control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION))
    # leaving forced mode touches only the EMS register; the leftover command
    # and setpoint are inert in self-consumption
    assert addresses(writes) == [(13049, 0)]
    assert inv.settings.charge_command is ChargeCommand.DISCHARGE
    assert mode(inv) is BatteryMode.SELF_CONSUMPTION


@pytest.mark.parametrize("power", [None, -1, 12001])
async def test_forced_power_must_be_within_the_max(
    inv: SungrowInverter, writes: list[WriteEvent], power: int | None
) -> None:
    with pytest.raises(PowerOutOfRangeError):
        await inv.battery_control.apply(
            DesiredState(BatteryMode.FORCED_CHARGE, power_w=power)
        )
    assert writes == []


async def test_effective_only_modes_cannot_be_requested(inv: SungrowInverter) -> None:
    with pytest.raises(ValueError, match="forced_stop"):
        await inv.battery_control.apply(DesiredState(BatteryMode.FORCED_STOP))


async def test_unknown_max_power_refuses_to_plan(
    unit: MockModbusUnit, sleeps: Sleeps, writes: list[WriteEvent]
) -> None:
    unit.input[4999 + 1] = 0xFFFF  # no nominal power ...
    unit.input[5627] = 0xFFFF  # ... and no BDC rating
    inverter = SungrowInverter(unit)
    inverter.battery_control = BatteryControl(inverter, sleep=sleeps)
    await inverter.async_update()
    assert inverter.battery_max_power_w is None
    with pytest.raises(SettingsUnavailableError):
        await inverter.battery_control.apply(DesiredState(BatteryMode.HOLD))
    assert writes == []


# -- freshness, failures, verification ------------------------------------------


async def test_stale_settings_are_re_read_before_planning(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    unit.holding[33046] = 1  # changed behind our back
    inv._refreshed["battery_limits"] -= 60  # and our snapshot is a minute old
    await inv.battery_control.apply(DesiredState(BatteryMode.NO_CHARGE))
    assert [e.address for e in unit.read_events][:1] == [33046]  # refreshed first
    assert writes == []  # ... and found nothing to write


async def test_fresh_settings_are_not_re_read(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    await inv.battery_control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION))
    assert unit.read_events == []


async def test_unreadable_settings_refuse_to_plan(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    inv._refreshed["settings"] -= 60
    unit.fail_read(13017, ServerDeviceFailureError())
    with pytest.raises(SettingsUnavailableError, match="settings"):
        await inv.battery_control.apply(DesiredState(BatteryMode.HOLD))
    assert writes == []


async def test_rejected_write_keeps_the_earlier_ones(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    unit.fail_write(33047, IllegalDataValueError())
    with pytest.raises(WriteRejectedError) as info:
        await inv.battery_control.apply(DesiredState(BatteryMode.HOLD))
    assert info.value.field == "battery_limits.max_discharge_power"
    assert addresses(writes) == [(33046, 1)]  # the charge fence stands
    assert [w.field for w in info.value.report.writes] == ["max_charge_power"]
    assert info.value.report.verified is False
    assert unit.holding[13049] == 0  # the mode register was never reached


async def test_verification_failure_names_the_field(
    inv: SungrowInverter, unit: MockModbusUnit, sleeps: Sleeps
) -> None:
    def refuse_silently(event: WriteEvent) -> None:
        if event.address == 13049:
            unit.holding[13049] = 0  # the inverter ignores the write

    unit.on_write(refuse_silently)
    with pytest.raises(VerificationError) as info:
        await inv.battery_control.apply(
            DesiredState(BatteryMode.FORCED_CHARGE, power_w=1000)
        )
    assert info.value.field == "settings.ems_mode"
    assert info.value.expected is EmsMode.COMPULSORY
    assert info.value.actual is EmsMode.SELF_CONSUMPTION
    assert sleeps.delays == [0.5]
    assert [w.field for w in info.value.report.writes] == [
        "forced_power",
        "charge_command",
        "ems_mode",
    ]


async def test_verify_can_be_skipped(
    inv: SungrowInverter, unit: MockModbusUnit, sleeps: Sleeps
) -> None:
    report = await inv.battery_control.apply(
        DesiredState(BatteryMode.HOLD), verify=False
    )
    assert report.verified is False and len(report.writes) == 2
    assert sleeps.delays == []
    assert unit.read_events == []


async def test_verify_refreshes_only_the_touched_components(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    await inv.battery_control.apply(DesiredState(BatteryMode.HOLD))
    assert [e.address for e in unit.read_events] == [33046]  # not settings
    unit.read_events.clear()
    await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_CHARGE, power_w=500)
    )
    assert [e.address for e in unit.read_events] == [33046, 13017]


async def test_calls_are_serialised(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    control = inv.battery_control
    await asyncio.gather(
        control.apply(DesiredState(BatteryMode.HOLD)),
        control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION)),
    )
    # one sequence completes before the next begins: fence, fence, restore, restore
    assert addresses(writes) == [(33046, 1), (33047, 1), (33046, 1200), (33047, 1200)]
    assert mode(inv) is BatteryMode.SELF_CONSUMPTION


# -- export limit, PV limit, start/stop ------------------------------------------


async def test_set_export_limit_writes_value_then_enable(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    report = await inv.battery_control.set_export_limit(0)
    assert addresses(writes) == [(13073, 0), (13086, 0xAA)]
    assert report.verified and report.action == "export_limit"
    writes.clear()
    await inv.battery_control.set_export_limit(12000)
    assert addresses(writes) == [(13073, 12000)]  # already enabled
    writes.clear()
    await inv.battery_control.set_export_limit(None)
    assert addresses(writes) == [(13086, 0x55)]
    assert inv.settings.export_limit == 12000  # the value is left alone


async def test_export_limit_is_bounded_by_the_ratings(
    inv: SungrowInverter, writes: list[WriteEvent]
) -> None:
    with pytest.raises(PowerOutOfRangeError, match="0..15000"):
        await inv.battery_control.set_export_limit(15001)
    assert writes == []


async def test_export_limit_warns_when_the_ratio_overrides_it(
    inv: SungrowInverter, unit: MockModbusUnit, caplog: pytest.LogCaptureFixture
) -> None:
    unit.holding[13087] = 500  # 50.0 %
    await inv.async_refresh("settings")
    await inv.battery_control.set_export_limit(3000)
    assert "50.0 %" in caplog.text and "overrides" in caplog.text


async def test_set_pv_limitation(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    report = await inv.battery_control.set_pv_limitation(True)
    assert addresses(writes) == [(13017, 0xAA)]
    assert report.verified and inv.settings.pv_power_limitation is True
    writes.clear()
    assert (await inv.battery_control.set_pv_limitation(True)).writes == []
    await inv.battery_control.set_pv_limitation(False)
    assert addresses(writes) == [(13017, 0x55)]


async def test_start_and_stop_write_the_command_without_read_back(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    stop = await inv.battery_control.stop()
    start = await inv.battery_control.start()
    assert addresses(writes) == [(12999, 0xCE), (12999, 0xCF)]
    assert stop.action == "stop" and start.action == "start"
    assert stop.verified is False and unit.read_events == []
    assert stop.as_dict()["writes"][0]["value"] == 0xCE


# -- effective mode --------------------------------------------------------------


@pytest.mark.parametrize(
    ("ems", "command", "charge", "discharge", "expected"),
    [
        (0, 0xCC, 1200, 1200, BatteryMode.SELF_CONSUMPTION),
        (0, 0xBB, 1000, 1000, BatteryMode.SELF_CONSUMPTION),  # leftover command
        (0, 0xCC, 1, 1200, BatteryMode.NO_CHARGE),
        (0, 0xCC, 1200, 1, BatteryMode.NO_DISCHARGE),
        (0, 0xCC, 1, 1, BatteryMode.HOLD),
        (0, 0xCC, 0, 0, BatteryMode.HOLD),  # raw 0 counts as fenced too
        (2, 0xAA, 1200, 1200, BatteryMode.FORCED_CHARGE),
        (2, 0xBB, 1200, 1200, BatteryMode.FORCED_DISCHARGE),
        (2, 0xCC, 1200, 1200, BatteryMode.FORCED_STOP),
        (2, 0x11, 1200, 1200, BatteryMode.UNKNOWN),
        (3, 0xCC, 1200, 1200, BatteryMode.EXTERNAL_EMS),
        (4, 0xCC, 1200, 1200, BatteryMode.VPP),
        (0, 0xCC, 0xFFFF, 1200, BatteryMode.UNKNOWN),  # limit not implemented
    ],
)
async def test_effective_mode_table(
    inv: SungrowInverter,
    unit: MockModbusUnit,
    ems: int,
    command: int,
    charge: int,
    discharge: int,
    expected: BatteryMode,
) -> None:
    unit.holding[13049] = ems
    unit.holding[13050] = command
    unit.holding[33046] = charge
    unit.holding[33047] = discharge
    await inv.async_refresh("settings", "battery_limits")
    assert mode(inv) is expected


async def test_effective_mode_is_none_before_a_read(unit: MockModbusUnit) -> None:
    assert SungrowInverter(unit).effective_battery_mode is None


async def test_self_consumption_is_doubted_when_the_running_state_disagrees(
    inv: SungrowInverter, unit: MockModbusUnit, caplog: pytest.LogCaptureFixture
) -> None:
    unit.input[12999] = 0x0800  # running in compulsory mode
    await inv.async_update_realtime()
    assert inv.settings.ems_mode is EmsMode.SELF_CONSUMPTION
    assert mode(inv) is BatteryMode.UNKNOWN
    assert "may not be served" in caplog.text


async def test_running_state_lag_after_our_own_write_is_tolerated(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    """Live, the running state stayed "compulsory mode" for a few seconds
    after leaving forced mode; that must not read as unknown."""
    await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_CHARGE, power_w=1000)
    )
    unit.input[12999] = 0x0800  # the inverter now reports compulsory mode
    await inv.battery_control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION))
    await inv.async_update_realtime()  # running state still lags
    assert mode(inv) is BatteryMode.SELF_CONSUMPTION
    inv.battery_control._last_write_at -= 120  # type: ignore[operator]
    assert mode(inv) is BatteryMode.UNKNOWN  # the lag has outlived the grace
