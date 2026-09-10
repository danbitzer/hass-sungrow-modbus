"""The top-level device object: an SH-T inverter reached through a ``ModbusUnit``."""

from __future__ import annotations

import logging
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
    Settings,
    StartPower,
)
from .exceptions import UnsupportedModelError
from .models import ShtModel, model_for
from .report import UpdateReport

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
"""Components a firmware may refuse; a refusal at setup drops them."""

DEFAULT_FENCE_POWER_W = 10
"""The battery power limit (W) that counts as "fenced": the register's minimum."""


async def _optional[C: Component](component: C) -> C | None:
    """Read an optional sub-system; None if this device does not serve it."""
    try:
        await component.async_update()
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
        model: ShtModel | None = None,
        battery_max_power_w: int | None = None,
        fence_power_w: int = DEFAULT_FENCE_POWER_W,
    ) -> None:
        self._unit = unit
        self.model: ShtModel | None = model
        self._battery_max_power_w = battery_max_power_w
        self.fence_power_w = fence_power_w

        # Read once at setup.
        self.identity = Identity(unit)
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

        self._realtime: tuple[str, ...] | None = None
        self._slow: tuple[str, ...] = ()

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
        """Read the identity block only and gate on the model.

        Raises ``UnsupportedModelError`` for a non-SH-T device type code, and
        the modbus-connection error if the device cannot be read.
        """
        identity = Identity(unit)
        await identity.async_update()
        code = identity.device_type_code
        if code is None:
            raise UnsupportedModelError(0xFFFF)
        model = model_for(code)
        return ProbeResult(
            device_type_code=code,
            model=model,
            serial=identity.serial or None,
            nominal_power_w=_as_int(identity.nominal_power),
            bdc_rated_power_w=_as_int(identity.bdc_rated_power),
            protocol_version=identity.protocol_version_text,
            arm_version=identity.arm_version or None,
            dsp_version=identity.dsp_version or None,
        )

    async def _async_setup(self) -> None:
        """Read what never changes and settle which sub-systems are served.

        Runs from the first update, and again on the next one if the device
        was unreachable then.
        """
        await self.identity.async_update()
        code = self.identity.device_type_code
        if code is None:
            raise UnsupportedModelError(0xFFFF)
        detected = model_for(code)
        if self.model is not None and self.model.code != code:
            _LOGGER.warning(
                "Configured model %s but the inverter reports %s; using %s",
                self.model.name,
                detected.name,
                detected.name,
            )
        self.model = detected

        if detected.mppt < 3:
            self.ac_dc.restrict_fields(
                [n for n in AcDc.declared_fields if not n.startswith("mppt3")]
            )

        firmware = await _optional(FirmwareInfo(self._unit))
        # A WiNet-S answers the block with blank strings rather than refusing it.
        self.firmware = None if firmware is None or firmware.empty else firmware

        for name in _OPTIONAL:
            component: Component | None = getattr(self, name)
            if component is not None and await _optional(component) is None:
                _LOGGER.info("Inverter does not serve %s; skipping it", name)
                setattr(self, name, None)

        self._realtime = _REALTIME
        self._slow = tuple(n for n in _SLOW if getattr(self, n) is not None)

    async def _ensure_setup(self) -> None:
        if self._realtime is None:
            await self._async_setup()

    # -- polling -------------------------------------------------------------

    async def _async_poll(
        self, names: tuple[str, ...], report: UpdateReport
    ) -> UpdateReport:
        """Read each named sub-system on its own, recording what happened."""
        for name in names:
            component: Component = getattr(self, name)
            try:
                await component.async_update(notify=False)
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
        return report

    def _notify(self, report: UpdateReport) -> None:
        """Fire the listeners of everything this update refreshed."""
        for name in report.updated:
            component: Component = getattr(self, name)
            component.notify()

    async def async_update_realtime(self) -> UpdateReport:
        """Refresh the fast-changing measurements."""
        await self._ensure_setup()
        assert self._realtime is not None
        report = await self._async_poll(self._realtime, UpdateReport())
        self._notify(report)
        return report

    async def async_update_settings(self) -> UpdateReport:
        """Refresh the settings, battery limits, energy counters and alarms."""
        await self._ensure_setup()
        report = await self._async_poll(self._slow, UpdateReport())
        self._notify(report)
        return report

    async def async_update(self) -> UpdateReport:
        """Refresh every polled sub-system."""
        await self._ensure_setup()
        assert self._realtime is not None
        report = await self._async_poll(self._realtime, UpdateReport())
        await self._async_poll(self._slow, report)
        self._notify(report)
        return report

    async def async_read_raw(self) -> dict[str, dict[int, int | bool]]:
        """Every register this device reads, undecoded — for diagnostics."""
        await self._ensure_setup()
        assert self._realtime is not None
        names = ["identity"]
        if self.firmware is not None:
            names.append("firmware")
        names += [*self._realtime, *self._slow]
        raw: dict[str, dict[int, int | bool]] = {}
        for name in names:
            component: Component = getattr(self, name)
            read = await component.async_read_raw(notify=False)
            for space, values in read.items():
                raw.setdefault(space, {}).update(values)
        return {space: dict(sorted(values.items())) for space, values in raw.items()}

    # -- derived -------------------------------------------------------------

    @property
    def battery_max_power_w(self) -> int | None:
        """The battery power limit to restore: the option, else BDC rated power."""
        if self._battery_max_power_w is not None:
            return self._battery_max_power_w
        return _as_int(self.identity.bdc_rated_power)

    @property
    def polled_components(self) -> tuple[str, ...]:
        """The names of every component the update methods poll."""
        return (*(self._realtime or ()), *self._slow)


def _as_int(value: float | None) -> int | None:
    return None if value is None else int(value)
