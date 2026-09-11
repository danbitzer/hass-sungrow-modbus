"""The top-level device object: an SH-T inverter reached through a ``ModbusUnit``."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from modbus_connection import (
    IllegalDataAddressError,
    IllegalFunctionError,
    ModbusConnectionError,
    ModbusError,
    ModbusTimeoutError,
)
from modbus_connection.model import Component

from .components import (
    AcDc,
    Alarms,
    AplShadow,
    Backup,
    Battery,
    BatteryLimits,
    BatteryPower,
    Control,
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
from .const import NARROW_HOLDING_RANGES, NARROW_INPUT_RANGES
from .control import BatteryControl
from .enums import BatteryMode
from .exceptions import SungrowError
from .models import ShtModel, model_for
from .report import Raw, UpdateReport

if TYPE_CHECKING:
    from modbus_connection import ModbusUnit

_LOGGER = logging.getLogger(__name__)

_REALTIME: tuple[str, ...] = (
    "ac_dc",
    "flows",
    "grid_phases",
    "meter",
    "backup",
    "battery",
    "battery_power",
)
_SLOW: tuple[str, ...] = (
    "settings",
    "battery_limits",
    "energy",
    "start_power",
    "apl_shadow",
    "alarms",
)
_OPTIONAL: tuple[str, ...] = ("start_power", "apl_shadow", "alarms")
"""Polled components a firmware may refuse; a refusal at setup drops them."""

DEFAULT_FENCE_POWER_W = 10
"""The battery power limit (W) that counts as "fenced": the register's minimum."""


def _narrow(component: Component) -> bool:
    """Re-plan a component against the map split at every documented hole.

    Returns False when it already uses it. The plan is rebuilt through
    ``restrict_fields`` with the fields it already reads: with nothing
    dropped the framework keeps the ranges just assigned and only
    invalidates the cached plan.
    """
    narrow = (
        NARROW_INPUT_RANGES
        if component.register_space == "input"
        else NARROW_HOLDING_RANGES
    )
    if component.register_ranges is narrow:
        return False
    component.register_ranges = narrow
    component.restrict_fields(list(component.resolved_fields))
    return True


async def _read(component: Component, *, collect_raw: bool = False) -> Raw | None:
    """Read one component; on a refused merged block, once more on the narrow map.

    A WiNet-S serves the documented holes inside a block; a stricter link
    may refuse a read that spans one, and then the M1 map, split at every
    hole, still works. A refusal on the narrow map is a real one.
    """
    try:
        if collect_raw:
            return await component.async_read_raw(notify=False)
        await component.async_update(notify=False)
        return None
    except IllegalDataAddressError:
        if not _narrow(component):
            raise
        _LOGGER.info(
            "%s: a merged block was refused; re-planned on the narrow map",
            type(component).__name__,
        )
        if collect_raw:
            return await component.async_read_raw(notify=False)
        await component.async_update(notify=False)
        return None


async def _optional[C: Component](component: C) -> C | None:
    """Read an optional sub-system; None if this device does not serve it."""
    try:
        await _read(component)
    except (IllegalDataAddressError, IllegalFunctionError):
        return None
    return component


@dataclass(frozen=True)
class ProbeResult:
    """What ``SungrowInverter.async_probe`` learned from the identity block."""

    device_type_code: int
    model: ShtModel
    serial: str | None
    nominal_power_w: int | None
    bdc_rated_power_w: int | None
    protocol_version: str | None
    arm_version: str | None
    dsp_version: str | None


class SungrowInverter:
    """A Sungrow SH-T hybrid inverter.

    Construct it from a ``ModbusUnit`` (never a connection): the caller owns
    the link. The first update reads the identity block, gates on the model,
    settles which optional sub-systems the firmware serves, and fixes the
    poll lists. Nothing is ever written during setup.
    """

    def __init__(
        self,
        unit: ModbusUnit,
        *,
        battery_max_power_w: int | None = None,
        fence_power_w: int = DEFAULT_FENCE_POWER_W,
    ) -> None:
        self._unit = unit
        self.model: ShtModel | None = None
        """The detected model; set by the first update."""
        self._battery_max_power_w = battery_max_power_w
        self.fence_power_w = fence_power_w

        # Read once at setup.
        self.identity = Identity(unit)
        self.ratings: Ratings | None = Ratings(unit)
        self.firmware: FirmwareInfo | None = None

        # Polled fast.
        self.ac_dc = AcDc(unit)
        self.flows = Flows(unit)
        self.grid_phases = GridPhases(unit)
        self.meter = Meter(unit)
        self.backup = Backup(unit)
        self.battery = Battery(unit)
        self.battery_power = BatteryPower(unit)

        # Polled slowly.
        self.settings = Settings(unit)
        self.battery_limits = BatteryLimits(unit)
        self.energy = Energy(unit)
        self.start_power: StartPower | None = StartPower(unit)
        self.apl_shadow: AplShadow | None = AplShadow(unit)
        self.alarms: Alarms | None = Alarms(unit)

        # Write-only.
        self.control = Control(unit)

        self.battery_control = BatteryControl(self)
        """Guarded, ordered, verified writes: battery mode, export and PV limits."""

        self._realtime: tuple[str, ...] | None = None
        self._slow: tuple[str, ...] = ()
        self._refreshed: dict[str, float] = {}

    # -- setup ---------------------------------------------------------------

    @property
    def is_setup(self) -> bool:
        """Whether the first update has settled the model and poll lists."""
        return self._realtime is not None

    @property
    def modbus_unit(self) -> ModbusUnit:
        return self._unit

    @classmethod
    async def async_probe(cls, unit: ModbusUnit) -> ProbeResult:
        """Read the identity block (and the ratings, if served) and gate on the model.

        Raises ``UnsupportedModelError`` for a non-SH-T device type code, and
        the modbus-connection error if the device cannot be read.
        """
        identity = Identity(unit)
        await identity.async_update(notify=False)
        model = model_for(_device_type_code(identity))
        ratings = await _optional(Ratings(unit))
        return ProbeResult(
            device_type_code=model.code,
            model=model,
            serial=identity.serial or None,
            nominal_power_w=_as_int(identity.nominal_power),
            bdc_rated_power_w=(
                None if ratings is None else _as_int(ratings.bdc_rated_power)
            ),
            protocol_version=identity.protocol_version_text,
            arm_version=identity.arm_version or None,
            dsp_version=identity.dsp_version or None,
        )

    async def _async_setup(self) -> None:
        """Read what never changes and settle which sub-systems are served.

        Runs from the first update, and again on the next one if the device
        was unreachable then. Listeners are not fired: the poll that follows
        does that for what it refreshes.
        """
        await _read(self.identity)
        self.model = model_for(_device_type_code(self.identity))
        self._refreshed["identity"] = time.monotonic()

        if self.model.mppt < 3:
            self.ac_dc.restrict_fields(
                [n for n in AcDc.declared_fields if not n.startswith("mppt3")]
            )

        if self.ratings is not None and await _optional(self.ratings) is None:
            _LOGGER.info("Inverter does not serve the ratings block; skipping it")
            self.ratings = None

        firmware = await _optional(FirmwareInfo(self._unit))
        # A WiNet-S answers the block with blank strings rather than refusing it.
        self.firmware = None if firmware is None or firmware.empty else firmware

        for name in _OPTIONAL:
            component: Component | None = getattr(self, name)
            if component is not None and await _optional(component) is None:
                _LOGGER.info("Inverter does not serve %s; skipping it", name)
                setattr(self, name, None)

        self._realtime = tuple(n for n in _REALTIME if getattr(self, n) is not None)
        self._slow = tuple(n for n in _SLOW if getattr(self, n) is not None)

    async def _ensure_setup(self) -> tuple[str, ...]:
        if self._realtime is None:
            await self._async_setup()
        assert self._realtime is not None
        return self._realtime

    # -- polling -------------------------------------------------------------

    async def _async_poll(
        self,
        names: tuple[str, ...],
        report: UpdateReport,
        *,
        collect_raw: bool = False,
    ) -> UpdateReport:
        """Read each named sub-system on its own, recording what happened."""
        for name in names:
            component: Component = getattr(self, name)
            try:
                read = await _read(component, collect_raw=collect_raw)
            except ModbusConnectionError:
                raise  # the link is down; the rest would only wait for timeouts
            except ModbusTimeoutError as err:
                if not report.updated and not report.failed:
                    raise  # nothing answered yet: assume the rest time out too
                report.failed[name] = err
            except ModbusError as err:
                report.failed[name] = err
            else:
                report.updated.append(name)
                self._refreshed[name] = time.monotonic()
                if read is not None:
                    raw = report.raw if report.raw is not None else {}
                    for space, values in read.items():
                        # First read wins where blocks overlap (energy spans
                        # the flows, battery and grid-phase registers), so the
                        # dump matches the values the poll decoded.
                        target = raw.setdefault(space, {})
                        for address, word in values.items():
                            target.setdefault(address, word)
                    report.raw = raw
        return report

    def _notify(self, report: UpdateReport) -> None:
        """Fire the listeners of everything this update refreshed."""
        for name in report.updated:
            component: Component = getattr(self, name)
            component.notify()

    async def async_update_realtime(self, *, collect_raw: bool = False) -> UpdateReport:
        """Refresh the fast-changing measurements."""
        realtime = await self._ensure_setup()
        report = await self._async_poll(
            realtime, UpdateReport(), collect_raw=collect_raw
        )
        self._notify(report)
        return report

    async def async_update_settings(self, *, collect_raw: bool = False) -> UpdateReport:
        """Refresh the settings, battery limits, energy counters and alarms."""
        await self._ensure_setup()
        report = await self._async_poll(
            self._slow, UpdateReport(), collect_raw=collect_raw
        )
        self._notify(report)
        return report

    async def async_update(self, *, collect_raw: bool = False) -> UpdateReport:
        """Refresh every polled sub-system.

        With ``collect_raw`` the same reads also fill ``report.raw`` with the
        raw words, so one sweep serves both a refresh and a diagnostics dump.
        """
        realtime = await self._ensure_setup()
        report = await self._async_poll(
            realtime, UpdateReport(), collect_raw=collect_raw
        )
        await self._async_poll(self._slow, report, collect_raw=collect_raw)
        self._notify(report)
        return report

    async def async_refresh(
        self, *names: str, collect_raw: bool = False
    ) -> UpdateReport:
        """Refresh only the named components, e.g. ``settings`` and
        ``battery_limits`` before and after a write.

        Raises ``ValueError`` for a name that is not a polled component.
        """
        await self._ensure_setup()
        polled = self.polled_components
        unknown = [n for n in names if n not in polled]
        if unknown:
            raise ValueError(f"not polled components: {', '.join(unknown)}")
        report = await self._async_poll(names, UpdateReport(), collect_raw=collect_raw)
        self._notify(report)
        return report

    async def async_read_raw(self) -> Raw:
        """Every register this device reads, undecoded — for diagnostics.

        Reads the setup blocks too. A component that fails is left out
        rather than failing the dump, as in a poll.
        """
        realtime = await self._ensure_setup()
        names = ["identity"]
        if self.ratings is not None:
            names.append("ratings")
        if self.firmware is not None:
            names.append("firmware")
        names += [*realtime, *self._slow]
        report = await self._async_poll(tuple(names), UpdateReport(), collect_raw=True)
        self._notify(report)
        raw = report.raw or {}
        return {space: dict(sorted(values.items())) for space, values in raw.items()}

    def last_refresh(self, name: str) -> float | None:
        """``time.monotonic()`` of the component's last successful read, or None."""
        return self._refreshed.get(name)

    # -- derived -------------------------------------------------------------

    @property
    def battery_max_power_w(self) -> int | None:
        """The battery power limit the write layer restores.

        The ``battery_max_power_w`` option when given — the integration makes
        it an explicit setting, as mkaiser's package does — else the lower of
        the BDC rating and the nominal AC power (an SH15T reports a 30 kW BDC
        on a 15 kW unit). None until setup has read the identity, or when
        neither rating is reported; a caller that needs a write target must
        treat None as "unknown", never as zero.
        """
        if self._battery_max_power_w is not None:
            return self._battery_max_power_w
        candidates = [_as_int(self.identity.nominal_power)]
        if self.ratings is not None:
            candidates.append(_as_int(self.ratings.bdc_rated_power))
        known = [c for c in candidates if c is not None]
        return min(known) if known else None

    @property
    def effective_battery_mode(self) -> BatteryMode | None:
        """The battery mode the last-polled settings imply."""
        return self.battery_control.effective_mode()

    @property
    def polled_components(self) -> tuple[str, ...]:
        """The names of every component the update methods poll."""
        return (*(self._realtime or ()), *self._slow)


def _device_type_code(identity: Identity) -> int:
    code = identity.device_type_code
    if code is None:  # the field has no sentinel; only an unread block gives None
        raise SungrowError("the identity block has not been read")
    return code


def _as_int(value: float | None) -> int | None:
    return None if value is None else int(value)
