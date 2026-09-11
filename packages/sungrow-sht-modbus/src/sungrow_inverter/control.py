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

from modbus_connection import ModbusConnectionError, ModbusError, ModbusTimeoutError
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
    InvalidWriteValueError,
    PowerOutOfRangeError,
    SettingsUnavailableError,
    VerificationError,
    WriteRejectedError,
    WriteUncertainError,
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
"""After an EMS mode write of ours, how long the running state may lag it
before a disagreement counts as evidence (live: a few seconds)."""

_SETTINGS = ("settings", "battery_limits")
_FORCED_STATES = (InverterState.COMPULSORY_MODE, InverterState.EXTERNAL_EMS)
_NOT_SELF_CONSUMPTION = (EmsMode.COMPULSORY, EmsMode.EXTERNAL_EMS, EmsMode.VPP)


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
    at_or_below: bool = False
    """A fence: any value at or below the target already satisfies it."""


@dataclass(frozen=True)
class PlannedWrite:
    """One register a plan would touch, and whether it already holds the value."""

    component: str
    field: str
    current: Any
    target: Any
    skip: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "component": self.component,
            "field": self.field,
            "current": _plain(self.current),
            "target": _plain(self.target),
            "skip": self.skip,
        }


def _plain(value: Any) -> Any:
    return getattr(value, "value", value)


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
    resolved = component.resolved_fields.get(name)
    if resolved is None or not isinstance(resolved.field, RegisterField):
        return False
    try:
        return wire_words(resolved.field, current) == wire_words(resolved.field, target)
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
    ) -> None:
        self._inv = inverter
        self._settle = settle_s
        self._max_age = max_age_s
        self._grace = grace_s
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._ems_written_at: float | None = None

    @property
    def lock(self) -> asyncio.Lock:
        """The lock every call holds; a poller that must not interleave with a
        write sequence (the settings coordinator) takes it too."""
        return self._lock

    # -- planning ------------------------------------------------------------

    def _limits(self) -> tuple[int, int]:
        """``(full, fence)``: the restore target and the fence, in W."""
        full = self._inv.battery_max_power_w
        if full is None:
            raise SettingsUnavailableError(
                "battery max power is unknown: the ratings have not been read "
                "and no battery_max_power_w option is set"
            )
        fence = self._inv.fence_power_w
        if full % 10 or not fence <= full <= 10 * 0xFFFF:
            raise PowerOutOfRangeError(
                "battery max power (a multiple of 10 W)", full, fence, 10 * 0xFFFF
            )
        return full, fence

    def _power(self, desired: DesiredState, full: int) -> int:
        power = desired.power_w
        if power is None or not 0 <= power <= full:
            raise PowerOutOfRangeError(f"{desired.mode.value} power", power, 0, full)
        return int(power)

    def _plan(self, desired: DesiredState) -> list[_Step]:
        """The register targets a desired state needs, in write order.

        Order: restore the limits the target does not fence, fence the ones
        it does, then power, then command, then the EMS mode last — so a
        partial failure leaves the previous mode with a sane setpoint rather
        than a forced mode with a stale one. Two exceptions, both about a
        forced mode that is already running: leaving it writes the EMS mode
        *first* (the safe direction, before the limits are restored), and
        reversing it writes a STOP command before the new power so no prefix
        forces the old direction at the new magnitude.
        """
        if desired.mode not in DESIRED_BATTERY_MODES:
            raise ValueError(f"{desired.mode.value} cannot be requested")
        full, fence = self._limits()
        settings = self._inv.settings
        s, lim = "settings", "battery_limits"
        restore = [
            _Step(lim, "max_charge_power", full),
            _Step(lim, "max_discharge_power", full),
        ]
        to_self = _Step(s, "ems_mode", EmsMode.SELF_CONSUMPTION)
        leaving_forced = settings.ems_mode in _NOT_SELF_CONSUMPTION
        match desired.mode:
            case BatteryMode.SELF_CONSUMPTION:
                return [to_self, *restore] if leaving_forced else [*restore, to_self]
            case BatteryMode.FORCED_CHARGE | BatteryMode.FORCED_DISCHARGE:
                command = (
                    ChargeCommand.CHARGE
                    if desired.mode is BatteryMode.FORCED_CHARGE
                    else ChargeCommand.DISCHARGE
                )
                steps = list(restore)
                live = settings.ems_mode is EmsMode.COMPULSORY
                if live and settings.charge_command is not command:
                    steps.append(_Step(s, "charge_command", ChargeCommand.STOP))
                steps += [
                    _Step(s, "forced_power", self._power(desired, full)),
                    _Step(s, "charge_command", command),
                    _Step(s, "ems_mode", EmsMode.COMPULSORY),
                ]
                return steps
            case BatteryMode.NO_CHARGE:
                return [
                    _Step(lim, "max_discharge_power", full),
                    _Step(lim, "max_charge_power", fence, at_or_below=True),
                    to_self,
                ]
            case BatteryMode.NO_DISCHARGE:
                return [
                    _Step(lim, "max_charge_power", full),
                    _Step(lim, "max_discharge_power", fence, at_or_below=True),
                    to_self,
                ]
            case BatteryMode.HOLD:
                return [
                    _Step(lim, "max_charge_power", fence, at_or_below=True),
                    _Step(lim, "max_discharge_power", fence, at_or_below=True),
                    to_self,
                ]
        raise AssertionError("unreachable")  # pragma: no cover

    def _satisfied(self, step: _Step, current: Any) -> bool:
        component = self._component(step.component)
        if step.at_or_below and current is not None:
            try:
                return bool(float(current) <= float(step.target))
            except (TypeError, ValueError):
                return False
        return same_on_wire(component, step.field, current, step.target)

    def _doubted(self, step: _Step) -> bool:
        """Whether a matching EMS mode word must not be trusted.

        A WiNet-S answers 0 for a register it does not forward, and 0 is the
        self-consumption code; when the running state contradicts it the
        EMS mode is written regardless and the read-back decides.
        """
        return (
            step.component == "settings"
            and step.field == "ems_mode"
            and self.effective_mode() is BatteryMode.INCONSISTENT
        )

    def plan(self, desired: DesiredState) -> list[PlannedWrite]:
        """The writes ``apply`` would make against the last-polled settings.

        A preview: it neither refreshes the snapshot nor takes the lock.
        """
        planned: list[PlannedWrite] = []
        for step in self._plan(desired):
            current = getattr(self._component(step.component), step.field)
            skip = self._satisfied(step, current) and not self._doubted(step)
            planned.append(
                PlannedWrite(step.component, step.field, current, step.target, skip)
            )
        return planned

    # -- execution -----------------------------------------------------------

    async def _ensure_fresh(self, names: tuple[str, ...], max_age: float) -> None:
        """Re-read the components a plan compares against if they are stale."""
        now = time.monotonic()
        stale = tuple(
            n
            for n in names
            if (at := self._inv.last_refresh(n)) is None or now - at > max_age
        )
        if not stale:
            return
        try:
            report = await self._inv.async_refresh(*stale)
        except ModbusError as err:
            raise SettingsUnavailableError(f"could not read {stale}: {err}") from err
        if report.failed:
            raise SettingsUnavailableError("could not read " + ", ".join(report.failed))

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
            if self._satisfied(step, current) and not self._doubted(step):
                report.skipped.append(label)
                continue
            try:
                await component.write(step.field, step.target)
            except (ModbusTimeoutError, ModbusConnectionError) as err:
                # The value may have landed before the answer was lost.
                report.uncertain.append(label)
                raise WriteUncertainError(label, err, report) from err
            except ModbusError as err:
                raise WriteRejectedError(label, err, report) from err
            except ValueError as err:
                raise InvalidWriteValueError(
                    label, step.target, str(err), report
                ) from err
            _LOGGER.info("%s: %s -> %s", label, current, step.target)
            report.writes.append(
                WriteRecord(step.component, step.field, current, step.target)
            )
            if label == "settings.ems_mode":
                self._ems_written_at = time.monotonic()
        if verify and report.writes:
            await self._sleep(self._settle)
            # Re-read every component the plan touched, written or not: a
            # skipped step is only verified if its register is read again.
            touched = tuple(dict.fromkeys(s.component for s in steps))
            try:
                refreshed = await self._inv.async_refresh(*touched)
            except ModbusError as err:
                raise SettingsUnavailableError(
                    f"read-back failed: {err}", report
                ) from err
            if refreshed.failed:
                raise SettingsUnavailableError(
                    "read-back failed: " + ", ".join(refreshed.failed), report
                )
            # A register written twice (STOP, then the new direction) is
            # verified against its last target.
            final = {(s.component, s.field): s for s in steps}
            for step in final.values():
                actual = getattr(self._component(step.component), step.field)
                if not self._satisfied(step, actual):
                    raise VerificationError(
                        f"{step.component}.{step.field}", step.target, actual, report
                    )
            report.verified = True
        return report

    # -- public API ----------------------------------------------------------

    async def apply(
        self,
        desired: DesiredState,
        *,
        verify: bool = True,
        max_age_s: float | None = None,
    ) -> WriteReport:
        """Put the battery into ``desired``: writes only what differs.

        ``max_age_s`` overrides the freshness threshold for this call; a
        caller that has just polled the settings passes a generous value.
        """
        async with self._lock:
            await self._ensure_fresh(_SETTINGS, self._age(max_age_s))
            steps = self._plan(desired)
            return await self._execute(desired.mode.value, steps, verify=verify)

    def _age(self, max_age_s: float | None) -> float:
        return self._max_age if max_age_s is None else max_age_s

    async def set_export_limit(
        self,
        limit_w: int | None,
        *,
        enabled: bool = True,
        verify: bool = True,
        max_age_s: float | None = None,
    ) -> WriteReport:
        """Set the feed-in limitation.

        ``limit_w`` is the cap in watts; ``None`` lifts the limitation
        (disables it) without touching the value — a DNSP cap is restored by
        passing its watts, not None. With ``enabled=False`` the value is
        written and the limitation then disabled. The value is written
        before the enable, so enabling never applies a stale limit. The
        ratio register (13088) overrides the value when below 100 %, so it is
        written to 100 % alongside an enabled limit; the active power
        limitation (13089/13090) is a separate cap and only warned about.
        """
        async with self._lock:
            await self._ensure_fresh(("settings",), self._age(max_age_s))
            settings = self._inv.settings
            s = "settings"
            if limit_w is None:
                steps = [_Step(s, "export_limit_enabled", False)]
            else:
                low, high = self._export_bounds()
                if not low <= limit_w <= high:
                    raise PowerOutOfRangeError("export limit", limit_w, low, high)
                steps = [_Step(s, "export_limit", int(limit_w))]
                if enabled:
                    ratio = settings.feed_in_ratio
                    if ratio is not None and ratio < 100.0:
                        _LOGGER.info(
                            "feed-in ratio is %.1f %%; raising it to 100 %% so the "
                            "%d W limit applies",
                            ratio,
                            limit_w,
                        )
                        steps.append(_Step(s, "feed_in_ratio", 100.0))
                steps.append(_Step(s, "export_limit_enabled", enabled))
                apl_ratio = settings.active_power_limit_ratio
                if (
                    settings.active_power_limit_enabled
                    and apl_ratio is not None
                    and apl_ratio < 100.0
                ):
                    _LOGGER.warning(
                        "active power limitation is enabled at %.1f %%; it caps the "
                        "inverter independently of the %d W export limit",
                        apl_ratio,
                        limit_w,
                    )
            return await self._execute("export_limit", steps, verify=verify)

    def _export_bounds(self) -> tuple[int, int]:
        """The feed-in limit range: the ratings, else the nominal power."""
        ratings = self._inv.ratings
        low = high = None
        if ratings is not None:
            low, high = ratings.export_limit_min, ratings.export_limit_max
        if high is None:
            high = self._inv.identity.nominal_power
        return (
            int(low) if low is not None else 0,
            int(high) if high is not None else 0xFFFF,
        )

    async def set_pv_limitation(
        self, limit: bool, *, verify: bool = True, max_age_s: float | None = None
    ) -> WriteReport:
        """Stop (True) or allow (False) PV generation (reg 13018, SH-T only)."""
        async with self._lock:
            await self._ensure_fresh(("settings",), self._age(max_age_s))
            steps = [_Step("settings", "pv_power_limitation", bool(limit))]
            return await self._execute("pv_limitation", steps, verify=verify)

    async def start(self) -> WriteReport:
        """Boot the inverter (reg 13000 = 0xCF). No read-back: it takes minutes.

        The EMS mode and charge command survive a shutdown, so a start
        resumes whatever forced mode was left behind; put the battery into
        ``self_consumption`` before a ``stop`` unless that is wanted.
        """
        return await self._command(StartStop.START)

    async def stop(self) -> WriteReport:
        """Shut the inverter down (reg 13000 = 0xCE). It will not restart
        itself, and a stopped hybrid does not enter backup mode."""
        return await self._command(StartStop.STOP)

    async def _command(self, command: StartStop) -> WriteReport:
        async with self._lock:
            report = WriteReport(action=command.name.lower())
            previous = self._inv.flows.running_state
            _LOGGER.warning(
                "Writing %s to the inverter (was %s)", command.name, previous
            )
            try:
                await self._inv.control.write("start_stop", command)
            except (ModbusTimeoutError, ModbusConnectionError) as err:
                report.uncertain.append("control.start_stop")
                raise WriteUncertainError("control.start_stop", err, report) from err
            except ModbusError as err:
                raise WriteRejectedError("control.start_stop", err, report) from err
            report.writes.append(
                WriteRecord("control", "start_stop", previous, command)
            )
            return report

    # -- state ---------------------------------------------------------------

    def effective_mode(self) -> BatteryMode | None:
        """The battery mode the polled settings imply; None before a read.

        A ``self_consumption`` EMS mode is only believed when the running
        state (once polled) agrees: a WiNet-S answers 0 for a register it
        does not forward, and 0 is that mode's code. The running state lags
        a mode change by a few seconds, so a disagreement within ``grace_s``
        of an EMS write of ours is not counted. ``INCONSISTENT`` means the
        settings do not add up: a doubted EMS word, a forced mode with no
        command, or a limit that is not served. Pure and silent.
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
                    None: BatteryMode.INCONSISTENT,
                }[settings.charge_command]
            case EmsMode.EXTERNAL_EMS:
                return BatteryMode.EXTERNAL_EMS
            case EmsMode.VPP:
                return BatteryMode.VPP
        state = self._inv.flows.running_state
        recent = (
            self._ems_written_at is not None
            and time.monotonic() - self._ems_written_at < self._grace
        )
        if state in _FORCED_STATES and not recent:
            # Not logged here: callers read this on every state write. The
            # integration logs the transition once, from its poll.
            return BatteryMode.INCONSISTENT
        limits = self._inv.battery_limits
        charge, discharge = limits.max_charge_power, limits.max_discharge_power
        if charge is None or discharge is None:
            return BatteryMode.INCONSISTENT
        fence = self._inv.fence_power_w
        charge_fenced, discharge_fenced = charge <= fence, discharge <= fence
        if charge_fenced and discharge_fenced:
            return BatteryMode.HOLD
        if charge_fenced:
            return BatteryMode.NO_CHARGE
        if discharge_fenced:
            return BatteryMode.NO_DISCHARGE
        return BatteryMode.SELF_CONSUMPTION
