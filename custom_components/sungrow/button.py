"""Buttons: start and stop the inverter."""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import SungrowConfigEntry
from .control import async_start_inverter, async_stop_inverter
from .entity import SungrowEntity, SungrowEntityDescription

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


class SungrowButtonDescription(SungrowEntityDescription, ButtonEntityDescription):
    """A command."""


BUTTONS: tuple[SungrowButtonDescription, ...] = (
    SungrowButtonDescription(
        key="start_inverter",
        translation_key="start_inverter",
        report_name=None,
        entity_category=EntityCategory.CONFIG,
    ),
    SungrowButtonDescription(
        key="stop_inverter",
        translation_key="stop_inverter",
        report_name=None,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SungrowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the buttons."""
    runtime = entry.runtime_data
    async_add_entities(
        SungrowButton(runtime.realtime, description) for description in BUTTONS
    )


class SungrowButton(SungrowEntity, ButtonEntity):
    """Start or stop the inverter; logged with the user who pressed it."""

    entity_description: SungrowButtonDescription

    async def async_press(self) -> None:
        runtime = self.coordinator.config_entry.runtime_data
        user = self._context.user_id if self._context is not None else None
        if self.entity_description.key == "stop_inverter":
            await async_stop_inverter(runtime, user)
        else:
            await async_start_inverter(runtime, user)
