"""The base entity: one typed attribute off the device, per coordinator."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from modbus_connection import ModbusError

from sungrow_inverter import SungrowInverter

from .const import DOMAIN
from .coordinator import SungrowCoordinator
from .errors import raise_for_write_error

_LOGGER = logging.getLogger(__name__)


def _always(device: SungrowInverter) -> bool:
    return True


@dataclass(frozen=True, kw_only=True)
class SungrowEntityDescription(EntityDescription):
    """What every Sungrow entity description carries."""

    report_name: str | tuple[str, ...] | None
    """The component(s) the value comes from, as the update report names
    them; None for a setup-time block that is never polled (identity,
    ratings). A value derived from several components names them all."""
    poll: str = "realtime"
    """Which coordinator refreshes it: ``realtime`` or ``settings``."""
    exists: Callable[[SungrowInverter], bool] = _always
    """Whether this inverter serves the value (optional blocks, MPPT count)."""

    @property
    def report_names(self) -> tuple[str, ...]:
        """``report_name`` as a tuple; empty for a setup-time value."""
        name = self.report_name
        if name is None:
            return ()
        return (name,) if isinstance(name, str) else name


class SungrowEntity(CoordinatorEntity[SungrowCoordinator]):
    """An entity reading one attribute off the device object."""

    _attr_has_entity_name = True
    entity_description: SungrowEntityDescription

    def __init__(
        self, coordinator: SungrowCoordinator, description: SungrowEntityDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.serial}_{description.key}"
        self._attr_device_info = coordinator.device_info

    @property
    def device(self) -> SungrowInverter:
        return self.coordinator.device

    def _components_updated(self) -> bool:
        """Whether every component the value needs refreshed in the last poll."""
        updated = self.coordinator.data.updated
        return all(name in updated for name in self.entity_description.report_names)

    @property
    def available(self) -> bool:
        """Unavailable when a component the value needs failed its last poll."""
        return super().available and self._components_updated()

    async def async_write(self, component: str, field: str, value: Any) -> None:
        """Write one register under the control lock, read it back, publish.

        For the raw control entities. The guarded sequences live in the
        library's ``BatteryControl``; this is the single-register path.
        """
        device = self.device
        label = f"{component}.{field}"
        target = getattr(device, component)
        previous = getattr(target, field)
        async with device.battery_control.lock:
            try:
                await target.write(field, value)
            except Exception as err:  # noqa: BLE001 - mapped to HA errors
                raise_for_write_error(label, value, err)
            _LOGGER.info("%s: %s -> %s", label, previous, value)
            try:
                report = await device.async_refresh(component)
                failure: BaseException | None = next(iter(report.failed.values()), None)
            except ModbusError as err:
                failure = err
        if failure is not None:
            # The write was answered; only the read-back failed. The cache
            # may not match the wire now: forget it and poll again.
            device.invalidate(component)
            if isinstance(failure, ModbusError):
                self.coordinator.async_mark_failed({component}, failure)
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="readback_failed",
                translation_placeholders={"field": label, "error": str(failure)},
            ) from failure
        self.coordinator.async_apply_snapshot(component)
