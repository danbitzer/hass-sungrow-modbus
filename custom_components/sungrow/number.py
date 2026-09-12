"""Numbers: the raw writable setpoints and limits (settings poll)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from sungrow_inverter import SungrowInverter

from . import SungrowConfigEntry
from .const import DOMAIN, SETTINGS
from .entity import SungrowEntity, SungrowEntityDescription

PARALLEL_UPDATES = 1

FALLBACK_POWER_W = 30000
FALLBACK_EXPORT_W = 65535


def battery_capability_w(device: SungrowInverter) -> int:
    """What the inverter can move: min(nominal, BDC rated); a fallback if unread."""
    known = [
        int(value)
        for value in (
            device.identity.nominal_power,
            device.ratings.bdc_rated_power if device.ratings is not None else None,
        )
        if value is not None
    ]
    return min(known) if known else FALLBACK_POWER_W


def _export_min(device: SungrowInverter) -> float:
    ratings = device.ratings
    return float(ratings.export_limit_min or 0) if ratings is not None else 0.0


def _export_max(device: SungrowInverter) -> float:
    ratings = device.ratings
    if ratings is not None and ratings.export_limit_max:
        return float(ratings.export_limit_max)
    return float(FALLBACK_EXPORT_W)


@dataclass(frozen=True, kw_only=True)
class SungrowNumberDescription(SungrowEntityDescription, NumberEntityDescription):
    """A writable register exposed as a number."""

    component: str
    field: str
    min_fn: Callable[[SungrowInverter], float] | None = None
    max_fn: Callable[[SungrowInverter], float] | None = None


def _number(
    key: str,
    component: str,
    field: str,
    *,
    low: float = 0,
    high: float = 0,
    step: float = 1,
    unit: str,
    device_class: NumberDeviceClass | None = None,
    **kw: Any,
) -> SungrowNumberDescription:
    return SungrowNumberDescription(
        key=key,
        translation_key=key,
        report_name=component,
        poll=SETTINGS,
        component=component,
        field=field,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        native_min_value=low,
        native_max_value=high,
        native_step=step,
        native_unit_of_measurement=unit,
        device_class=device_class,
        **kw,
    )


def _power(key: str, component: str, field: str, **kw: Any) -> SungrowNumberDescription:
    return _number(
        key,
        component,
        field,
        unit=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        **kw,
    )


NUMBERS: tuple[SungrowNumberDescription, ...] = (
    _power(
        "forced_power",
        "settings",
        "forced_power",
        step=100,
        max_fn=battery_capability_w,
    ),
    _power(
        "battery_max_charge_power",
        "battery_limits",
        "max_charge_power",
        low=10,
        step=10,
        max_fn=battery_capability_w,
    ),
    _power(
        "battery_max_discharge_power",
        "battery_limits",
        "max_discharge_power",
        low=10,
        step=10,
        max_fn=battery_capability_w,
    ),
    _number("battery_min_soc", "settings", "min_soc", high=50, unit=PERCENTAGE),
    _number(
        "battery_max_soc", "settings", "max_soc", low=50, high=100, unit=PERCENTAGE
    ),
    _number(
        "battery_reserved_soc_for_backup",
        "settings",
        "backup_reserve_soc",
        high=100,
        unit=PERCENTAGE,
    ),
    _power(
        "export_power_limit",
        "settings",
        "export_limit",
        step=100,
        min_fn=_export_min,
        max_fn=_export_max,
    ),
    _number(
        "active_power_limit_ratio",
        "settings",
        "active_power_limit_ratio",
        high=100,
        unit=PERCENTAGE,
        exists=lambda d: d.settings.active_power_limit_ratio is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SungrowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the numbers this inverter serves."""
    runtime = entry.runtime_data
    async_add_entities(
        SungrowNumber(runtime.settings, description)
        for description in NUMBERS
        if description.exists(runtime.device)
    )


class SungrowNumber(SungrowEntity, NumberEntity):
    """A writable register."""

    entity_description: SungrowNumberDescription

    @property
    def native_value(self) -> float | None:
        d = self.entity_description
        value = getattr(getattr(self.device, d.component), d.field)
        return None if value is None else float(value)

    @property
    def native_min_value(self) -> float:
        fn = self.entity_description.min_fn
        return fn(self.device) if fn else super().native_min_value

    @property
    def native_max_value(self) -> float:
        fn = self.entity_description.max_fn
        return fn(self.device) if fn else super().native_max_value

    async def async_set_native_value(self, value: float) -> None:
        d = self.entity_description
        if (
            d.field == "active_power_limit_ratio"
            and value == 0
            and self.device.apl_shadow is not None
            and self.device.apl_shadow.apl_shutdown_at_zero
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="would_stop_inverter"
            )
        await self.async_write(
            d.component, d.field, int(value) if value.is_integer() else value
        )
