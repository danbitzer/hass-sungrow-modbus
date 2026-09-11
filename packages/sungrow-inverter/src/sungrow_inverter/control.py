"""The guarded write layer: battery mode, export limit, PV limit, start/stop.

Every call plans the registers a target state needs, writes only the ones
whose last-polled value differs (compared at register resolution), in an
order that leaves the inverter in a sane state if a write fails, and reads
the settings back to prove the inverter took them. One lock per inverter
serialises calls.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from modbus_connection import ModbusError
from modbus_connection.model import Component, RegisterField

from .enums import (
    DESIRED_BATTERY_MODES,
    BatteryMode,
    ChargeCommand,
    EmsMode,
    InverterState,
    StartStop,
)
from .exceptions import (
    PowerOutOfRangeError,
    SettingsUnavailableError,
    VerificationError,
    WriteRejectedError,
)
from .report import WriteRecord, WriteReport

if TYPE_CHECKING:
    from .inverter import SungrowInverter

_LOGGER = logging.getLogger(__name__)

DEFAULT_SETTLE_S = 0.5
"""Seconds between the last write and the read-back."""
DEFAULT_MAX_AGE_S = 30.0
"""A settings snapshot older than this is re-read before planning."""
DEFAULT_GRACE_S = 60.0
"""After a write of ours, how long the running state may lag the EMS mode
before a disagreement counts as evidence (live: a few seconds)."""

_SETTINGS = ("settings", "battery_limits")


@dataclass(frozen=True)
class DesiredState:
    """The battery behaviour a caller asks for."""

    mode: BatteryMode
    power_w: int | None = None
    """Required for ``forced_charge`` / ``forced_discharge``; ignored otherwise."""


@dataclass(frozen=True)
class _Step:
    component: str
    field: str
    target: Any


def wire_words(field: RegisterField[Any], value: Any) -> list[int]:
    """The register words ``value`` would put on the wire for ``field``.

    Runs the field's write validator first, so a bool on an enable register
    and a float on a scaled one compare the way a write would encode them.
    """
    if callable(field.writable):
        value = field.writable(value)
    return field.encode(value)


def same_on_wire(component: Component, name: str, current: Any, target: Any) -> bool:
    """Whether ``current`` (last polled) already equals ``target`` on the wire."""
    if current is None:
        return False
    field = component.declared_fields[name]
    assert isinstance(field, RegisterField)
    try:
        return wire_words(field, current) == wire_words(field, target)
    except (ValueError, TypeError):
        return False


class BatteryControl:
    """Guarded, ordered, verified writes to one inverter."""

    def __init__(
        self,
        inverter: SungrowInverter,
        *,
        settle_s: float = DEFAULT_SETTLE_S,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        grace_s: float = DEFAULT_GRACE_S,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inv = inverter
        self._settle = settle_s
        self._max_age = max_age_s
        self._grace = grace_s
        self._sleep = sleep
        self._clock = clock
        self._lock = asyncio.Lock()
        self._last_write_at: float | None = None

    # -- planning ------------------------------------------------------------

    def _limits(self) -> tuple[int, int]:
        """``(full, fence)``: the restore target and the fence, in W."""
        full = self._inv.battery_max_power_w
        if full is None:
            raise SettingsUnavailableError(
                "battery max power is unknown: the ratings have not been read "
                "and no battery_max_power_w option is set"
            )
        return full, self._inv.fence_power_w

    def _power(self, desired: DesiredState, full: int) -> int:
        power = desired.power_w
        if power is None:
            raise PowerOutOfRangeError(f"{desired.mode.value} power", None, 0, full)
        if not 0 <= power <= full:
            raise PowerOutOfRangeError(f"{desired.mode.value} power", power, 0, full)
        return int(power)

    def _plan(self, desired: DesiredState) -> list[_Step]:
        """The register targets a desired state needs, in write order.

        Order: restore the limits the target does not fence, fence the ones
        it does, then power, then command, then the EMS mode last — so a
        partial failure leaves the previous mode with a sane setpoint rather
        than a forced mode with a stale one.
        """
        if desired.mode not in DESIRED_BATTERY_MODES:
            raise ValueError(f"{desired.mode.value} cannot be requested")
        full, fence = self._limits()
        s, lim = "settings", "battery_limits"
        match desired.mode:
            case BatteryMode.SELF_CONSUMPTION:
                return [
                    _Step(lim, "max_charge_power", full),
                    _Step(lim, "max_discharge_power", full),
                    _Step(s, "ems_mode", EmsMode.SELF_CONSUMPTION),
                ]
            case BatteryMode.FORCED_CHARGE | BatteryMode.FORCED_DISCHARGE:
                command = (
                    ChargeCommand.CHARGE
                    if desired.mode is BatteryMode.FORCED_CHARGE
                    else ChargeCommand.DISCHARGE
                )
                return [
                    _Step(lim, "max_charge_power", full),
                    _Step(lim, "max_discharge_power", full),
                    _Step(s, "forced_power", self._power(desired, full)),
                    _Step(s, "charge_command", command),
                    _Step(s, "ems_mode", EmsMode.COMPULSORY),
                ]
            case BatteryMode.NO_CHARGE:
                return [
                    _Step(lim, "max_discharge_power", full),
                    _Step(lim, "max_charge_power", fence),
                    _Step(s, "ems_mode", EmsMode.SELF_CONSUMPTION),
                ]
            case BatteryMode.HOLD:
                return [
                    _Step(lim, "max_charge_power", fence),
                    _Step(lim, "max_discharge_power", fence),
                    _Step(s, "ems_mode", EmsMode.SELF_CONSUMPTION),
                ]
        raise AssertionError("unreachable")  # pragma: no cover

    # -- execution -----------------------------------------------------------

    async def _ensure_fresh(self, *names: str) -> None:
        """Re-read the components a plan compares against if they are stale."""
        now = self._clock()
        stale = [
            n
            for n in names
            if (at := self._inv.last_refresh(n)) is None or now - at > self._max_age
        ]
        if not stale:
            return
        report = await self._inv.async_refresh(*stale)
        if report.failed:
            names_failed = ", ".join(report.failed)
            raise SettingsUnavailableError(f"could not read {names_failed}")

    def _component(self, name: str) -> Component:
        component: Component = getattr(self._inv, name)
        return component

    async def _execute(
        self, action: str, steps: list[_Step], *, verify: bool
    ) -> WriteReport:
        """Write the steps that differ, then read back and compare."""
        report = WriteReport(action=action)
        for step in steps:
            component = self._component(step.component)
            current = getattr(component, step.field)
            label = f"{step.component}.{step.field}"
            if same_on_wire(component, step.field, current, step.target):
                report.skipped.append(label)
                continue
            try:
                await component.write(step.field, step.target)
            except ModbusError as err:
                raise WriteRejectedError(label, err, report) from err
            except ValueError as err:
                raise PowerOutOfRangeError(label, step.target, 0, 0) from err
            _LOGGER.info("%s: %s -> %s", label, current, step.target)
            report.writes.append(
                WriteRecord(step.component, step.field, current, step.target)
            )
            self._last_write_at = self._clock()
        if verify and report.writes:
            await self._sleep(self._settle)
            # Re-read what was written; a skipped step's value is the one
            # just polled, and still the comparison target below.
            touched = tuple(dict.fromkeys(w.component for w in report.writes))
            refreshed = await self._inv.async_refresh(*touched)
            if refreshed.failed:
                raise SettingsUnavailableError(
                    "read-back failed: " + ", ".join(refreshed.failed)
                )
            for step in steps:
                component = self._component(step.component)
                actual = getattr(component, step.field)
                if not same_on_wire(component, step.field, actual, step.target):
                    raise VerificationError(
                        f"{step.component}.{step.field}", step.target, actual, report
                    )
            report.verified = True
        return report

    # -- public API ----------------------------------------------------------

    async def apply(self, desired: DesiredState, *, verify: bool = True) -> WriteReport:
        """Put the battery into ``desired``: writes only what differs."""
        async with self._lock:
            await self._ensure_fresh(*_SETTINGS)
            steps = self._plan(desired)
            return await self._execute(desired.mode.value, steps, verify=verify)

    async def set_export_limit(
        self, limit_w: int | None, *, verify: bool = True
    ) -> WriteReport:
        """Cap export at ``limit_w`` (feed-in limitation), or lift it with None.

        The value is written before the enable, so enabling never applies a
        stale limit. The ratio register (13088) takes precedence over the
        value when it is below 100 %; a warning says so.
        """
        async with self._lock:
            await self._ensure_fresh("settings")
            settings = self._inv.settings
            if limit_w is None:
                steps = [_Step("settings", "export_limit_enabled", False)]
            else:
                low, high = self._export_bounds()
                if not low <= limit_w <= high:
                    raise PowerOutOfRangeError("export limit", limit_w, low, high)
                ratio = settings.feed_in_ratio
                if ratio is not None and ratio < 100.0:
                    _LOGGER.warning(
                        "feed-in ratio is %.1f %%; it overrides the %d W limit",
                        ratio,
                        limit_w,
                    )
                steps = [
                    _Step("settings", "export_limit", int(limit_w)),
                    _Step("settings", "export_limit_enabled", True),
                ]
            return await self._execute("export_limit", steps, verify=verify)

    def _export_bounds(self) -> tuple[int, int]:
        ratings = self._inv.ratings
        low = high = None
        if ratings is not None:
            low = ratings.export_limit_min
            high = ratings.export_limit_max
        return (
            int(low) if low is not None else 0,
            int(high) if high is not None else 0xFFFF,
        )

    async def set_pv_limitation(
        self, limit: bool, *, verify: bool = True
    ) -> WriteReport:
        """Stop (True) or allow (False) PV generation (reg 13018, SH-T only)."""
        async with self._lock:
            await self._ensure_fresh("settings")
            steps = [_Step("settings", "pv_power_limitation", bool(limit))]
            return await self._execute("pv_limitation", steps, verify=verify)

    async def start(self) -> WriteReport:
        """Boot the inverter (reg 13000 = 0xCF). No read-back: it takes minutes."""
        return await self._command(StartStop.START)

    async def stop(self) -> WriteReport:
        """Shut the inverter down (reg 13000 = 0xCE). It will not restart itself."""
        return await self._command(StartStop.STOP)

    async def _command(self, command: StartStop) -> WriteReport:
        async with self._lock:
            report = WriteReport(action=command.name.lower())
            try:
                await self._inv.control.write("start_stop", command)
            except ModbusError as err:
                raise WriteRejectedError("control.start_stop", err, report) from err
            report.writes.append(WriteRecord("control", "start_stop", None, command))
            return report

    # -- state ---------------------------------------------------------------

    def effective_mode(self) -> BatteryMode | None:
        """The battery mode the polled settings imply; None before a read.

        A ``self_consumption`` EMS mode is only believed when the running
        state agrees: a WiNet-S answers 0 for a register it does not
        forward, and 0 is that mode's code. The running state lags a mode
        change by a few seconds, so a disagreement within ``grace_s`` of a
        write of ours is not counted.
        """
        settings = self._inv.settings
        ems = settings.ems_mode
        if ems is None:
            return None
        match ems:
            case EmsMode.COMPULSORY:
                return {
                    ChargeCommand.CHARGE: BatteryMode.FORCED_CHARGE,
                    ChargeCommand.DISCHARGE: BatteryMode.FORCED_DISCHARGE,
                    ChargeCommand.STOP: BatteryMode.FORCED_STOP,
                    None: BatteryMode.UNKNOWN,
                }[settings.charge_command]
            case EmsMode.EXTERNAL_EMS:
                return BatteryMode.EXTERNAL_EMS
            case EmsMode.VPP:
                return BatteryMode.VPP
        state = self._inv.flows.running_state
        recent = (
            self._last_write_at is not None
            and self._clock() - self._last_write_at < self._grace
        )
        if (
            state in (InverterState.COMPULSORY_MODE, InverterState.EXTERNAL_EMS)
            and not recent
        ):
            _LOGGER.warning(
                "EMS mode reads self-consumption but the running state is %s; "
                "the register may not be served",
                state.value,
            )
            return BatteryMode.UNKNOWN
        limits = self._inv.battery_limits
        charge, discharge = limits.max_charge_power, limits.max_discharge_power
        if charge is None or discharge is None:
            return BatteryMode.UNKNOWN
        fence = self._inv.fence_power_w
        charge_fenced, discharge_fenced = charge <= fence, discharge <= fence
        if charge_fenced and discharge_fenced:
            return BatteryMode.HOLD
        if charge_fenced:
            return BatteryMode.NO_CHARGE
        if discharge_fenced:
            return BatteryMode.NO_DISCHARGE
        return BatteryMode.SELF_CONSUMPTION
