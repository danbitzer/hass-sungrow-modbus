"""The guarded write layer against the mock: plan, guard, order, verify."""

from __future__ import annotations

import asyncio

import pytest
from modbus_connection import (
    IllegalDataValueError,
    ModbusTimeoutError,
    ServerDeviceFailureError,
)
from modbus_connection.mock import MockModbusUnit, WriteEvent

from sungrow_inverter import (
    DESIRED_BATTERY_MODES,
    BatteryControl,
    BatteryMode,
    ChargeCommand,
    DesiredState,
    EmsMode,
    InvalidWriteValueError,
    PowerOutOfRangeError,
    SettingsUnavailableError,
    SungrowInverter,
    VerificationError,
    WriteRejectedError,
    WriteUncertainError,
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
        "uncertain": [],
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
    assert info.value.report is not None
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
    assert info.value.report is not None
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


async def test_verify_re_reads_every_touched_component(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    await inv.battery_control.apply(DesiredState(BatteryMode.HOLD))
    # every component the plan touched is re-read, written or skipped
    assert [e.address for e in unit.read_events] == [33046, 13017]
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


async def test_set_export_limit_writes_ratio_then_value_then_enable(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    # the ratio register is the same limit in tenths of a percent of the
    # 15 kW nominal power and takes precedence: it is aligned to the target
    # first, then the watts, then the enable
    report = await inv.battery_control.set_export_limit(0)
    assert addresses(writes) == [(13087, 0), (13073, 0), (13086, 0xAA)]
    assert report.verified and report.action == "export_limit"
    writes.clear()
    await inv.battery_control.set_export_limit(12000)
    assert addresses(writes) == [(13087, 800), (13073, 12000)]  # already enabled
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


async def test_export_limit_aligns_a_ratio_that_would_override_it(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    unit.holding[13087] = 500  # 50.0 %: the ratio register takes precedence
    await inv.async_refresh("settings")
    report = await inv.battery_control.set_export_limit(3000)
    # aligned to the target's own ratio (20 % of 15 kW), never "raised to
    # 100 %": on a mirroring inverter that would read back as 15000 W
    assert addresses(writes) == [(13087, 200), (13073, 3000), (13086, 0xAA)]
    assert report.verified and inv.settings.feed_in_ratio == 20.0


def mirror_ratio_and_watts(unit: MockModbusUnit) -> None:
    """The SH-T keeps the feed-in ratio (13088) and value (13074) registers
    as two views of one setting: writing either updates both (live on an
    SH15T, 2026-09-13)."""

    def mirror(event: WriteEvent) -> None:
        value = int(event.values[0])
        if event.address == 13073:
            unit.holding[13087] = round(value / 15000 * 1000)
        elif event.address == 13087:
            unit.holding[13073] = round(value / 1000 * 15000)

    unit.on_write(mirror)


async def test_export_limit_on_a_mirroring_inverter_reasserts_silently(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    """The 2026-09-13 live failure: a 50 W cap was re-asserted every five
    minutes; the old routine saw the mirrored 0.3 % ratio as "below 100 %",
    raised it, and the inverter mirrored that back as 15000 W — lifting the
    cap for a five-minute export at negative feed-in on every other sweep,
    and failing verification on the ones between."""
    mirror_ratio_and_watts(unit)
    unit.holding[13086] = 0xAA
    await inv.async_refresh("settings")
    report = await inv.battery_control.set_export_limit(50)
    assert addresses(writes) == [(13087, 3), (13073, 50)]
    assert report.verified
    assert inv.settings.export_limit == 50 and inv.settings.feed_in_ratio == 0.3
    writes.clear()
    # the re-assert: nothing differs, nothing is written, nothing lifts
    report = await inv.battery_control.set_export_limit(50)
    assert writes == []
    assert set(report.skipped) == {
        "settings.export_limit",
        "settings.export_limit_enabled",
    }
    assert inv.settings.export_limit == 50
    # a watts value that is not a multiple of 0.1 % of nominal rounds on the
    # mirror; the ratio verifies within its tolerance
    writes.clear()
    report = await inv.battery_control.set_export_limit(100)
    assert addresses(writes) == [(13087, 7), (13073, 100)]
    assert report.verified and inv.settings.export_limit == 100


async def test_export_limit_warns_about_an_active_power_limit(
    inv: SungrowInverter, unit: MockModbusUnit, caplog: pytest.LogCaptureFixture
) -> None:
    unit.holding[13088] = 0xAA
    unit.holding[13089] = 300  # 30.0 %
    await inv.async_refresh("settings")
    await inv.battery_control.set_export_limit(3000)
    assert "active power limitation" in caplog.text and "30.0 %" in caplog.text


async def test_export_limit_can_write_the_value_then_disable(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    unit.holding[13086] = 0xAA  # currently enabled
    await inv.async_refresh("settings")
    await inv.battery_control.set_export_limit(12000, enabled=False)
    assert addresses(writes) == [(13073, 12000), (13086, 0x55)]
    assert inv.settings.export_limit == 12000
    assert inv.settings.export_limit_enabled is False


async def test_export_bounds_fall_back_to_the_nominal_power(
    unit: MockModbusUnit, sleeps: Sleeps, writes: list[WriteEvent]
) -> None:
    from modbus_connection import IllegalDataAddressError

    unit.fail_read(5627, IllegalDataAddressError(), register_type="input")
    inverter = SungrowInverter(unit, battery_max_power_w=12000)
    inverter.battery_control = BatteryControl(inverter, sleep=sleeps)
    await inverter.async_update()
    assert inverter.ratings is None
    with pytest.raises(PowerOutOfRangeError, match="0..15000"):
        await inverter.battery_control.set_export_limit(60000)
    assert writes == []


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
        (2, 0x11, 1200, 1200, BatteryMode.INCONSISTENT),
        (3, 0xCC, 1200, 1200, BatteryMode.EXTERNAL_EMS),
        (4, 0xCC, 1200, 1200, BatteryMode.VPP),
        (0, 0xCC, 0xFFFF, 1200, BatteryMode.INCONSISTENT),  # limit not implemented
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
    assert mode(inv) is BatteryMode.INCONSISTENT
    assert mode(inv) is BatteryMode.INCONSISTENT
    assert caplog.text == ""  # read on every state write: never logs


async def test_grace_is_armed_only_by_an_ems_write(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    unit.input[12999] = 0x0800
    await inv.async_update_realtime()
    assert mode(inv) is BatteryMode.INCONSISTENT
    await inv.battery_control.set_pv_limitation(True)  # not an EMS write
    assert mode(inv) is BatteryMode.INCONSISTENT


async def test_running_state_lag_after_our_own_write_is_tolerated(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    """Live, the running state stayed "compulsory mode" for a few seconds
    after leaving forced mode; that must not read as inconsistent."""
    await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_CHARGE, power_w=1000)
    )
    unit.input[12999] = 0x0800  # the inverter now reports compulsory mode
    await inv.battery_control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION))
    await inv.async_update_realtime()  # running state still lags
    assert mode(inv) is BatteryMode.SELF_CONSUMPTION
    inv.battery_control._ems_written_at -= 120  # type: ignore[operator]
    assert mode(inv) is BatteryMode.INCONSISTENT  # the lag has outlived the grace


# -- review fixes: direction flips, doubted EMS word, failure prefixes ---------


async def test_reversing_a_live_forced_mode_stops_it_before_the_new_power(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_CHARGE, power_w=2000)
    )
    writes.clear()
    await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_DISCHARGE, power_w=5000)
    )
    # STOP first, so no prefix charges at the discharge setpoint
    assert addresses(writes) == [(13050, 0xCC), (13051, 5000), (13050, 0xBB)]
    writes.clear()
    # same direction, new magnitude: only the power register
    await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_DISCHARGE, power_w=3000)
    )
    assert addresses(writes) == [(13051, 3000)]


async def test_failed_direction_write_never_forces_the_old_direction(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_CHARGE, power_w=2000)
    )
    count = 0

    def fail_second_command(event: WriteEvent) -> None:
        nonlocal count
        if event.address == 13050:
            count += 1
            if count == 1:  # the STOP has landed; refuse the DISCHARGE
                unit.fail_write(13050, IllegalDataValueError())

    unit.on_write(fail_second_command)
    with pytest.raises(WriteRejectedError) as info:
        await inv.battery_control.apply(
            DesiredState(BatteryMode.FORCED_DISCHARGE, power_w=5000)
        )
    assert info.value.field == "settings.charge_command"
    assert unit.holding[13050] == 0xCC  # stopped, not charging at 5000
    assert unit.holding[13051] == 5000
    await inv.async_refresh("settings")
    assert mode(inv) is BatteryMode.FORCED_STOP


async def test_leaving_a_forced_mode_writes_the_ems_mode_first(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    unit.holding[13049] = 2
    unit.holding[13050] = 0xAA
    unit.holding[33046] = 1  # a fence left behind
    await inv.async_refresh("settings", "battery_limits")
    unit.fail_write(33046, IllegalDataValueError())
    with pytest.raises(WriteRejectedError):
        await inv.battery_control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION))
    assert addresses(writes) == [(13049, 0)]  # left compulsory before restoring
    assert unit.holding[33046] == 1  # a failure leaves it fenced, never forced


@pytest.mark.parametrize(
    ("desired", "failing", "expect"),
    [
        # hold: a failure never leaves an unfenced window
        (DesiredState(BatteryMode.HOLD), 33046, {33046: 1200, 33047: 1200}),
        (DesiredState(BatteryMode.HOLD), 33047, {33046: 1, 33047: 1200}),
        # no_charge from a hold-like state: discharge restored first
        (DesiredState(BatteryMode.NO_CHARGE), 33046, {33046: 1200, 33047: 1200}),
        # forced charge from self-consumption: mode never reached
        (DesiredState(BatteryMode.FORCED_CHARGE, 1000), 13051, {13049: 0}),
        (DesiredState(BatteryMode.FORCED_CHARGE, 1000), 13050, {13049: 0, 13051: 1000}),
        (DesiredState(BatteryMode.FORCED_CHARGE, 1000), 13049, {13049: 0, 13050: 0xAA}),
    ],
)
async def test_every_failure_prefix_leaves_a_sane_state(
    inv: SungrowInverter,
    unit: MockModbusUnit,
    desired: DesiredState,
    failing: int,
    expect: dict[int, int],
) -> None:
    unit.fail_write(failing, IllegalDataValueError())
    with pytest.raises(WriteRejectedError):
        await inv.battery_control.apply(desired)
    for address, value in expect.items():
        assert unit.holding[address] == value, address


async def test_a_doubted_ems_word_is_written_regardless(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    """The dongle says EMS 0 but the inverter runs in compulsory mode."""
    unit.input[12999] = 0x0800
    await inv.async_update_realtime()
    assert mode(inv) is BatteryMode.INCONSISTENT
    planned = inv.battery_control.plan(DesiredState(BatteryMode.SELF_CONSUMPTION))
    assert [(p.field, p.skip) for p in planned] == [
        ("max_charge_power", True),
        ("max_discharge_power", True),
        ("ems_mode", False),
    ]
    report = await inv.battery_control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION))
    assert addresses(writes) == [(13049, 0)]
    assert report.verified


async def test_unanswered_write_is_reported_as_uncertain(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    unit.fail_write(33047, ModbusTimeoutError())
    with pytest.raises(WriteUncertainError) as info:
        await inv.battery_control.apply(DesiredState(BatteryMode.HOLD))
    report = info.value.report
    assert report is not None
    assert [w.field for w in report.writes] == ["max_charge_power"]
    assert report.uncertain == ["battery_limits.max_discharge_power"]
    assert report.as_dict()["uncertain"] == report.uncertain


async def test_failed_read_back_still_hands_over_the_report(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    unit.fail_read(33046, ServerDeviceFailureError())
    with pytest.raises(SettingsUnavailableError) as info:
        await inv.battery_control.apply(DesiredState(BatteryMode.HOLD))
    assert info.value.report is not None
    assert [w.field for w in info.value.report.writes] == [
        "max_charge_power",
        "max_discharge_power",
    ]
    assert unit.holding[33046] == 1 and unit.holding[33047] == 1


async def test_unreadable_freshness_read_is_a_control_error(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    inv._refreshed["settings"] -= 60
    unit.fail_read(13017, ModbusTimeoutError())
    with pytest.raises(SettingsUnavailableError):
        await inv.battery_control.apply(DesiredState(BatteryMode.HOLD))


async def test_max_power_must_be_a_multiple_of_ten(
    unit: MockModbusUnit, sleeps: Sleeps, writes: list[WriteEvent]
) -> None:
    inverter = SungrowInverter(unit, battery_max_power_w=9999)
    inverter.battery_control = BatteryControl(inverter, sleep=sleeps)
    await inverter.async_update()
    with pytest.raises(PowerOutOfRangeError, match="multiple of 10"):
        await inverter.battery_control.apply(DesiredState(BatteryMode.HOLD))
    assert writes == []


async def test_validator_rejections_keep_their_reason(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    inv._battery_max_power_w = 10 * 0xFFFF + 10  # past what the register holds
    with pytest.raises(PowerOutOfRangeError):
        await inv.battery_control.apply(DesiredState(BatteryMode.SELF_CONSUMPTION))
    steps = inv.battery_control._plan  # noqa: F841 - planning is where it fails
    inv._battery_max_power_w = 12000
    from sungrow_inverter.control import _Step

    with pytest.raises(InvalidWriteValueError, match="bool"):
        await inv.battery_control._execute(
            "x", [_Step("settings", "pv_power_limitation", 0xAA)], verify=False
        )


async def test_hold_is_write_free_against_zero_fences(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    """Numbat's blueprint wrote 0 W for months; 0 satisfies a 10 W fence."""
    unit.holding[33046] = 0
    unit.holding[33047] = 0
    await inv.async_refresh("battery_limits")
    assert mode(inv) is BatteryMode.HOLD
    report = await inv.battery_control.apply(DesiredState(BatteryMode.HOLD))
    assert writes == [] and report.writes == []
    await inv.battery_control.apply(DesiredState(BatteryMode.NO_CHARGE))
    assert addresses(writes) == [(33047, 1200)]  # only the discharge restore


async def test_no_discharge_mirrors_no_charge(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    await inv.battery_control.apply(DesiredState(BatteryMode.NO_DISCHARGE))
    assert addresses(writes) == [(33047, 1)]
    assert mode(inv) is BatteryMode.NO_DISCHARGE


def test_every_desired_plan_ends_with_the_ems_mode_unless_leaving_forced(
    inv: SungrowInverter,
) -> None:
    for desired_mode in DESIRED_BATTERY_MODES:
        steps = inv.battery_control._plan(DesiredState(desired_mode, power_w=1000))
        assert steps[-1].field == "ems_mode", desired_mode


async def test_apply_only_ever_writes_holding_settings_registers(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    control = inv.battery_control
    for desired in (
        DesiredState(BatteryMode.HOLD),
        DesiredState(BatteryMode.FORCED_CHARGE, 100),
        DesiredState(BatteryMode.FORCED_DISCHARGE, 100),
        DesiredState(BatteryMode.NO_CHARGE),
        DesiredState(BatteryMode.NO_DISCHARGE),
        DesiredState(BatteryMode.SELF_CONSUMPTION),
    ):
        await control.apply(desired)
    await control.set_export_limit(100)
    await control.set_export_limit(None)
    await control.set_pv_limitation(True)
    assert writes
    assert all(w.register_type == "holding" for w in writes)
    assert all(
        13017 <= w.address <= 13099 or 33046 <= w.address <= 33047 for w in writes
    )
    assert 12999 not in {w.address for w in writes}


async def test_apply_survives_a_concurrent_settings_poll(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    async def poll() -> None:
        for _ in range(20):
            await inv.async_update_settings()

    await asyncio.gather(
        inv.battery_control.apply(DesiredState(BatteryMode.FORCED_CHARGE, 3000)),
        poll(),
    )
    assert addresses(writes) == [(13051, 3000), (13050, 0xAA), (13049, 2)]
    assert mode(inv) is BatteryMode.FORCED_CHARGE


async def test_running_state_not_yet_polled_believes_the_settings(
    unit: MockModbusUnit, sleeps: Sleeps
) -> None:
    inverter = SungrowInverter(unit, battery_max_power_w=12000)
    inverter.battery_control = BatteryControl(inverter, sleep=sleeps)
    await inverter.async_update_settings()  # settings only; flows never read
    assert inverter.flows.running_state is None
    assert inverter.effective_battery_mode is BatteryMode.SELF_CONSUMPTION


async def test_per_call_max_age_skips_the_freshness_read(
    inv: SungrowInverter, unit: MockModbusUnit
) -> None:
    inv._refreshed["settings"] -= 60
    inv._refreshed["battery_limits"] -= 60
    await inv.battery_control.apply(
        DesiredState(BatteryMode.SELF_CONSUMPTION), max_age_s=3600
    )
    assert unit.read_events == []


async def test_stop_and_start_record_the_running_state(
    inv: SungrowInverter, caplog: pytest.LogCaptureFixture
) -> None:
    report = await inv.battery_control.stop()
    assert report.writes[0].previous is inv.flows.running_state
    assert "STOP" in caplog.text
    assert inv.battery_control.lock.locked() is False


async def test_a_failed_sequence_forgets_the_snapshot(
    inv: SungrowInverter, unit: MockModbusUnit, writes: list[WriteEvent]
) -> None:
    """After a write without an answer the cache may not match the wire, so
    the next call must re-read before it plans (or it could skip a write)."""
    unit.fail_write(13050, ModbusTimeoutError())
    with pytest.raises(WriteUncertainError):
        await inv.battery_control.apply(
            DesiredState(BatteryMode.FORCED_CHARGE, power_w=2000)
        )
    assert addresses(writes) == [(13051, 2000)]  # the power landed
    assert inv.last_refresh("settings") is None
    assert inv.last_refresh("battery_limits") is None
    assert inv.settings.forced_power == 0  # the stale cache, honestly stale

    unit.fail_write(13050, None)
    unit.read_events.clear()
    writes.clear()
    await inv.battery_control.apply(
        DesiredState(BatteryMode.FORCED_CHARGE, power_w=2000)
    )
    assert unit.read_events[0].address == 13017  # re-read before planning
    assert addresses(writes) == [(13050, 0xAA), (13049, 2)]  # power not rewritten
    assert inv.settings.forced_power == 2000
