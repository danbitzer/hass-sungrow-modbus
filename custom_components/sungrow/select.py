"""Selects: the raw EMS mode and charge command registers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from sungrow_inverter import ChargeCommand, EmsMode

from . import SungrowConfigEntry
from .const import SETTINGS
from .entity import SungrowEntity, SungrowEntityDescription

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SungrowSelectDescription(SungrowEntityDescription, SelectEntityDescription):
    """An enum register exposed as a select; options are the members' names."""

    field: str
    enum: type[IntEnum]


def _select(key: str, field: str, enum: type[IntEnum]) -> SungrowSelectDescription:
    return SungrowSelectDescription(
        key=key,
        translation_key=key,
        report_name="settings",
        poll=SETTINGS,
        field=field,
        enum=enum,
        entity_category=EntityCategory.CONFIG,
        options=[member.name.lower() for member in enum],
    )


SELECTS: tuple[SungrowSelectDescription, ...] = (
    _select("ems_mode", "ems_mode", EmsMode),
    _select("charge_command", "charge_command", ChargeCommand),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SungrowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the selects."""
    runtime = entry.runtime_data
    async_add_entities(
        SungrowSelect(runtime.settings, description) for description in SELECTS
    )


class SungrowSelect(SungrowEntity, SelectEntity):
    """An enum register."""

    entity_description: SungrowSelectDescription

    @property
    def current_option(self) -> str | None:
        value = getattr(self.device.settings, self.entity_description.field)
        return None if value is None else str(value.name).lower()

    async def async_select_option(self, option: str) -> None:
        d = self.entity_description
        await self.async_write("settings", d.field, d.enum[option.upper()])
