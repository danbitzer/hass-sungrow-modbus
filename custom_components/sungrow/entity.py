"""The base entity: one typed attribute off the device, per coordinator."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from sungrow_inverter import SungrowInverter

from .coordinator import SungrowCoordinator


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
