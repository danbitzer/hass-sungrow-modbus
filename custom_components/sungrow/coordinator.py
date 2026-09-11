"""Poll the inverter on two intervals and publish each poll's report.

The settings coordinator polls under ``device.battery_control.lock`` so a
poll never interleaves with a control call's write sequence. The lock is
not re-entrant: code running inside a control call must never await
``async_refresh()`` on the settings coordinator (it would wait on itself);
it publishes what it read back with ``async_apply_snapshot`` instead. A
poll that finds the lock taken returns the last report unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import timedelta
from functools import cached_property
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from modbus_connection import ModbusConnectionError, ModbusError, ModbusTimeoutError

from sungrow_inverter import (
    BatteryMode,
    SungrowError,
    SungrowInverter,
    UnsupportedModelError,
    UpdateReport,
)

from .const import CONF_SERIAL, DOMAIN, MANUFACTURER

if TYPE_CHECKING:
    from . import SungrowConfigEntry

_LOGGER = logging.getLogger(__name__)

STUCK_LINK_TIMEOUTS = 3
"""Consecutive polls where nothing answered before the link is dropped."""


class SungrowCoordinator(DataUpdateCoordinator[UpdateReport]):
    """Run one of the device's update methods on its own interval."""

    config_entry: SungrowConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SungrowConfigEntry,
        device: SungrowInverter,
        kind: str,
        poll: Callable[[], Awaitable[UpdateReport]],
        interval: timedelta,
        *,
        count_timeouts: bool = False,
        lock: asyncio.Lock | None = None,
        watch_battery_mode: bool = False,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{entry.title} {kind}",
            update_interval=interval,
        )
        self.device = device
        self.kind = kind
        self._poll = poll
        self._count_timeouts = count_timeouts
        self._lock = lock
        self._watch_battery_mode = watch_battery_mode
        self._timeouts = 0
        self._failed: frozenset[str] = frozenset()
        self._inconsistent = False

    @property
    def serial(self) -> str:
        """The serial the entry was created for; the key of every unique id."""
        return str(self.config_entry.data[CONF_SERIAL])

    async def _async_update_data(self) -> UpdateReport:
        if self._lock is not None and self._lock.locked() and self.data is not None:
            _LOGGER.debug("%s poll skipped: a control call is in progress", self.kind)
            return self.data
        try:
            if self._lock is not None:
                async with self._lock:
                    report = await self._poll()
            else:
                report = await self._poll()
        except UnsupportedModelError as err:
            raise ConfigEntryError(str(err)) from err
        except (ModbusTimeoutError, ModbusConnectionError) as err:
            if self._count_timeouts:
                self._timeouts += 1
                if self._timeouts >= STUCK_LINK_TIMEOUTS:
                    # A link that is up but unresponsive: drop it so the next
                    # poll opens a fresh one.
                    _LOGGER.warning(
                        "No answer in %d polls; dropping the link", self._timeouts
                    )
                    await self.device.modbus_unit.disconnect()
                    self._timeouts = 0
            raise UpdateFailed(str(err)) from err
        except (ModbusError, SungrowError) as err:
            raise UpdateFailed(str(err)) from err
        self._timeouts = 0
        if not report.updated:
            errors = list(report.failed.values())
            raise UpdateFailed(
                f"no component answered: {errors[0] if errors else 'nothing polled'}"
            )
        for name in sorted(report.failed.keys() - self._failed):
            _LOGGER.warning("Failed to read %s: %s", name, report.failed[name])
        for name in sorted(self._failed - report.failed.keys()):
            _LOGGER.info("%s reads again", name)
        self._failed = frozenset(report.failed)
        if self._watch_battery_mode:
            self._log_battery_mode_transition()
        return report

    def _log_battery_mode_transition(self) -> None:
        """Warn once when the settings stop adding up, and once when they do."""
        inconsistent = self.device.effective_battery_mode is BatteryMode.INCONSISTENT
        if inconsistent and not self._inconsistent:
            settings = self.device.settings
            _LOGGER.warning(
                "The battery settings are inconsistent (EMS mode %s, command %s, "
                "running state %s); a register may not be served",
                settings.ems_mode.name.lower() if settings.ems_mode else None,
                settings.charge_command.name.lower()
                if settings.charge_command
                else None,
                self.device.flows.running_state,
            )
        elif self._inconsistent and not inconsistent:
            _LOGGER.info("The battery settings are consistent again")
        self._inconsistent = inconsistent

    @callback
    def async_apply_snapshot(self, *names: str) -> None:
        """Publish values the control layer just read back, without a poll.

        The report's timestamp moves to now for the named components, so
        freshness checks see the read-back, not the last poll.
        """
        if self.data is None:
            return
        refreshed = set(names)
        report = replace(
            self.data,
            updated=sorted(set(self.data.updated) | refreshed),
            failed={k: v for k, v in self.data.failed.items() if k not in refreshed},
            at=time.monotonic(),
        )
        self.async_set_updated_data(report)

    @cached_property
    def device_info(self) -> DeviceInfo:
        """Describe the inverter to the device registry."""
        device = self.device
        identity = device.identity
        model = device.model
        firmware = device.firmware
        sw_version = (
            firmware.inverter_firmware
            if firmware is not None and firmware.inverter_firmware
            else identity.arm_version
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self.serial)},
            name=f"{MANUFACTURER} {model.name}" if model is not None else MANUFACTURER,
            manufacturer=MANUFACTURER,
            model=model.name if model is not None else None,
            model_id=f"0x{model.code:04X}" if model is not None else None,
            serial_number=self.serial,
            sw_version=sw_version or None,
        )
