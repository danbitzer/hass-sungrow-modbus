"""The guarded control calls, shared by the actions and the buttons.

Every call goes through the library's ``BatteryControl`` (the lock, the
plan, the read-back) and then publishes what the inverter holds: the
components the call re-read are pushed to the settings coordinator as a
snapshot, so ``sensor.battery_mode`` and the numbers move at once.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from sungrow_inverter import BatteryMode, DesiredState, WriteReport

from .const import FRESHNESS_MARGIN_S
from .errors import raise_for_control_error

if TYPE_CHECKING:
    from . import SungrowRuntime

_LOGGER = logging.getLogger(__name__)

type ControlCall = Callable[[float | None], Awaitable[WriteReport]]


def max_age_s(runtime: SungrowRuntime) -> float | None:
    """A settings snapshot from the last poll is fresh enough to plan on."""
    interval = runtime.settings.update_interval
    return None if interval is None else interval.total_seconds() + FRESHNESS_MARGIN_S


async def async_run(runtime: SungrowRuntime, call: ControlCall) -> WriteReport:
    """Run one control call, map its errors, publish the outcome."""
    try:
        report = await call(max_age_s(runtime))
    except Exception as err:  # noqa: BLE001 - every kind is mapped
        published = getattr(err, "report", None)
        if published is not None:
            # Show what the inverter actually holds after a partial sequence.
            runtime.settings.async_apply_snapshot(*_touched(published))
        raise_for_control_error(err)
    _LOGGER.info(
        "%s: %d written, %d skipped, verified=%s",
        report.action,
        len(report.writes),
        len(report.skipped),
        report.verified,
    )
    if report.verified:
        runtime.settings.async_apply_snapshot(*_touched(report))
    elif report.writes:
        await runtime.settings.async_refresh()
    return report


def _touched(report: WriteReport) -> set[str]:
    return {w.component for w in report.writes} | {
        label.split(".", 1)[0] for label in report.skipped
    }


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
    _LOGGER.warning("Start inverter requested (user %s)", user_id)
    report = await async_run(
        runtime, lambda age: runtime.device.battery_control.start()
    )
    await runtime.realtime.async_refresh()
    return report


async def async_stop_inverter(
    runtime: SungrowRuntime, user_id: str | None
) -> WriteReport:
    """Put the battery into self-consumption, then shut the inverter down.

    The EMS mode survives a shutdown: a later start would otherwise resume
    a forced mode with a stale setpoint.
    """
    _LOGGER.warning("Stop inverter requested (user %s)", user_id)
    restored = await async_set_battery_mode(
        runtime, BatteryMode.SELF_CONSUMPTION, None, verify=True
    )
    report = await async_run(runtime, lambda age: runtime.device.battery_control.stop())
    report.writes = restored.writes + report.writes
    report.skipped = restored.skipped + report.skipped
    await runtime.realtime.async_refresh()
    return report
