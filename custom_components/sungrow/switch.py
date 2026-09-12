"""Switches: the 0xAA/0x55 enable registers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SungrowConfigEntry
from .const import DOMAIN, SETTINGS
from .entity import SungrowEntity, SungrowEntityDescription

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class SungrowSwitchDescription(SungrowEntityDescription, SwitchEntityDescription):
    """An enable register exposed as a switch."""

    field: str


def _switch(key: str, field: str, **kw: Any) -> SungrowSwitchDescription:
    return SungrowSwitchDescription(
        key=key,
        translation_key=key,
        report_name="settings",
        poll=SETTINGS,
        field=field,
        entity_category=EntityCategory.CONFIG,
        **kw,
    )


SWITCHES: tuple[SungrowSwitchDescription, ...] = (
    _switch("export_limit_enabled", "export_limit_enabled"),
    _switch("backup_mode", "backup_mode"),
    _switch("pv_limitation", "pv_power_limitation"),
    _switch(
        "active_power_limit_enabled",
        "active_power_limit_enabled",
        exists=lambda d: d.settings.active_power_limit_ratio is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SungrowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switches this inverter serves."""
    runtime = entry.runtime_data
    async_add_entities(
        SungrowSwitch(runtime.settings, description)
        for description in SWITCHES
        if description.exists(runtime.device)
    )


class SungrowSwitch(SungrowEntity, SwitchEntity):
    """An enable register."""

    entity_description: SungrowSwitchDescription

    @property
    def is_on(self) -> bool | None:
        value = getattr(self.device.settings, self.entity_description.field)
        return None if value is None else bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        field = self.entity_description.field
        settings, shadow = self.device.settings, self.device.apl_shadow
        if (
            field == "active_power_limit_enabled"
            and settings.active_power_limit_ratio == 0
            and shadow is not None
            and shadow.apl_shutdown_at_zero
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="would_stop_inverter"
            )
        await self.async_write("settings", field, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.async_write("settings", self.entity_description.field, False)
