"""The guarded control calls, shared by the actions and the buttons.

Every call goes through the library's ``BatteryControl`` (the lock, the
plan, the read-back) and then publishes what the inverter holds: the
components the call re-read are pushed to the settings coordinator as a
snapshot, so ``sensor.battery_mode`` and the numbers move at once.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from modbus_connection import ModbusError

from sungrow_inverter import (
    BatteryMode,
    DesiredState,
    SettingsUnavailableError,
    VerificationError,
    WriteReport,
    WriteUncertainError,
)

from .const import FRESHNESS_MARGIN_S
from .errors import raise_for_control_error

if TYPE_CHECKING:
    from . import SungrowRuntime

_LOGGER = logging.getLogger(__name__)

type ControlCall = Callable[[float | None], Coroutine[Any, Any, WriteReport]]


def max_age_s(runtime: SungrowRuntime) -> float | None:
    """A settings snapshot from the last poll is fresh enough to plan on."""
    interval = runtime.settings.update_interval
    return None if interval is None else interval.total_seconds() + FRESHNESS_MARGIN_S


async def async_run(
    runtime: SungrowRuntime, call: ControlCall, *, publish: bool = True
) -> WriteReport:
    """Run one control call to its end, whatever happens to the caller.

    An automation in ``mode: restart`` cancels its in-flight service call
    when it is re-triggered; the write sequence, its read-back and the
    publication must still run to the end, or the inverter is left between
    two states and HA does not know it.
    """
    task = runtime.settings.hass.async_create_task(
        _async_run(runtime, call, publish=publish)
    )
    return await asyncio.shield(task)


async def _async_run(
    runtime: SungrowRuntime, call: ControlCall, *, publish: bool
) -> WriteReport:
    """Run one control call, map its errors, publish the outcome.

    What gets published is always something the inverter answered: the
    library's read-back (a verified call, or a verification failure), or a
    fresh read of the touched components. A write that got no answer or
    was refused leaves the cache suspect, so those are re-read too; if that
    read fails as well, the coordinator keeps its last honest poll.

    A read-back that fails (a WiNet-S under contention answers exception 4
    in bursts) does not fail the call outright: the touched components are
    re-read and the call is planned again once. If nothing is left to
    write, the first attempt's writes are confirmed; if something is, it
    is written and verified as usual.
    """
    try:
        report = await call(max_age_s(runtime))
    except (SettingsUnavailableError, WriteUncertainError) as err:
        if not publish or not await _publish_after_error(runtime, err):
            raise_for_control_error(err)
        first = err.report
        assert first is not None
        _LOGGER.warning(
            "%s: %s; re-read the settings and planning again", first.action, err
        )
        try:
            second = await call(max_age_s(runtime))
        except Exception as again:  # noqa: BLE001 - every kind is mapped
            await _publish_after_error(runtime, again)
            raise_for_control_error(again)
        report = WriteReport(
            action=second.action,
            writes=first.writes + second.writes,
            skipped=second.skipped,
            verified=second.verified or not second.writes,
            at=first.at,
        )
    except Exception as err:  # noqa: BLE001 - every kind is mapped
        if publish:
            await _publish_after_error(runtime, err)
        raise_for_control_error(err)
    _LOGGER.info(
        "%s: %d written, %d skipped, verified=%s",
        report.action,
        len(report.writes),
        len(report.skipped),
        report.verified,
    )
    if publish and report.writes:
        touched = _touched(report)
        if report.verified:
            runtime.settings.async_apply_snapshot(*touched)
        else:
            await _reread(runtime, touched)
    return report


async def _publish_after_error(runtime: SungrowRuntime, err: BaseException) -> bool:
    """Publish what can honestly be published after a failed call.

    Returns whether the touched components were read afresh.
    """
    report = getattr(err, "report", None)
    if report is None:
        return False
    touched = _touched(report)
    if isinstance(err, VerificationError):
        # The library read everything back before raising: that is fresh.
        runtime.settings.async_apply_snapshot(*touched)
        return True
    field = getattr(err, "field", None)
    if isinstance(field, str):
        touched.add(field.split(".", 1)[0])
    return await _reread(runtime, touched)


async def _reread(runtime: SungrowRuntime, names: set[str]) -> bool:
    """Read the named components again (outside the lock) and publish them."""
    if not names:
        return True
    try:
        refreshed = await runtime.device.async_refresh(*sorted(names))
    except ModbusError as err:
        _LOGGER.warning("Could not re-read %s after a control call: %s", names, err)
        runtime.settings.async_mark_failed(names, err)
        return False
    read = names - refreshed.failed.keys()
    if read:
        runtime.settings.async_apply_snapshot(*sorted(read))
    for name, failure in refreshed.failed.items():
        if name in names:
            runtime.settings.async_mark_failed({name}, failure)
    return not (names & refreshed.failed.keys())


def _touched(report: WriteReport) -> set[str]:
    return (
        {w.component for w in report.writes}
        | {label.split(".", 1)[0] for label in report.skipped}
        | {label.split(".", 1)[0] for label in report.uncertain}
    )


def response(runtime: SungrowRuntime, report: WriteReport) -> dict[str, Any]:
    mode = runtime.device.effective_battery_mode
    return {**report.as_dict(), "battery_mode": mode.value if mode else None}


async def async_set_battery_mode(
    runtime: SungrowRuntime, mode: BatteryMode, power_w: int | None, *, verify: bool
) -> WriteReport:
    control = runtime.device.battery_control
    return await async_run(
        runtime,
        lambda age: control.apply(
            DesiredState(mode, power_w), verify=verify, max_age_s=age
        ),
    )


async def async_set_export_limit(
    runtime: SungrowRuntime, limit_w: int | None, *, enabled: bool, verify: bool
) -> WriteReport:
    control = runtime.device.battery_control
    return await async_run(
        runtime,
        lambda age: control.set_export_limit(
            limit_w, enabled=enabled, verify=verify, max_age_s=age
        ),
    )


async def async_set_pv_limitation(
    runtime: SungrowRuntime, limit: bool, *, verify: bool
) -> WriteReport:
    control = runtime.device.battery_control
    return await async_run(
        runtime,
        lambda age: control.set_pv_limitation(limit, verify=verify, max_age_s=age),
    )


async def async_start_inverter(
    runtime: SungrowRuntime, user_id: str | None
) -> WriteReport:
    """Boot the inverter. No settings read follows: it answers nothing for
    minutes; the measurement poll shows the running state coming back."""
    _LOGGER.warning("Start inverter requested (user %s)", user_id)
    control = runtime.device.battery_control
    report = await async_run(runtime, lambda age: control.start(), publish=False)
    await runtime.realtime.async_refresh()
    return report


async def async_stop_inverter(
    runtime: SungrowRuntime, user_id: str | None
) -> WriteReport:
    """Put the battery into self-consumption, then shut the inverter down.

    The EMS mode survives a shutdown: a later start would otherwise resume
    a forced mode with a stale setpoint. If the restore fails the stop is
    not attempted.
    """
    _LOGGER.warning("Stop inverter requested (user %s)", user_id)
    restored = await async_set_battery_mode(
        runtime, BatteryMode.SELF_CONSUMPTION, None, verify=True
    )
    control = runtime.device.battery_control
    stopped = await async_run(runtime, lambda age: control.stop(), publish=False)
    await runtime.realtime.async_refresh()
    return WriteReport(
        action="stop",
        writes=restored.writes + stopped.writes,
        skipped=restored.skipped + stopped.skipped,
        uncertain=restored.uncertain + stopped.uncertain,
        verified=restored.verified,
        at=restored.at,
    )
