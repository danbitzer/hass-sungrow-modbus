"""Binary sensors: the power-flow bits, grid presence, alarms."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from sungrow_inverter import InverterState, PowerFlow, SungrowInverter

from . import SungrowConfigEntry
from .const import REALTIME, SETTINGS
from .entity import SungrowEntity, SungrowEntityDescription

OFF_GRID_STATES = frozenset(
    {
        InverterState.OFF_GRID,
        InverterState.OFF_GRID_CHARGE,
        InverterState.MICROGRID_OPERATION,
    }
)
GRID_PRESENT_VOLTAGE = 50.0


@dataclass(frozen=True, kw_only=True)
class SungrowBinarySensorDescription(
    SungrowEntityDescription, BinarySensorEntityDescription
):
    """A binary sensor backed by one device attribute."""

    is_on_fn: Callable[[SungrowInverter], bool | None]


def _flag(bit: PowerFlow) -> Callable[[SungrowInverter], bool | None]:
    def is_on(device: SungrowInverter) -> bool | None:
        flow = device.flows.power_flow
        return None if flow is None else bool(flow & bit)

    return is_on


def _grid_connected(device: SungrowInverter) -> bool | None:
    """Grid present: not running off-grid, and grid voltage on phase A."""
    state = device.flows.running_state
    voltage = device.ac_dc.phase_a_voltage
    if state is None and voltage is None:
        return None
    if state in OFF_GRID_STATES:
        return False
    if voltage is None:
        return None
    return voltage > GRID_PRESENT_VOLTAGE


def _power_flow(
    key: str, bit: PowerFlow, **kw: object
) -> SungrowBinarySensorDescription:
    return SungrowBinarySensorDescription(
        key=key,
        translation_key=key,
        report_name="flows",
        is_on_fn=_flag(bit),
        **kw,  # type: ignore[arg-type]
    )


BINARY_SENSORS: tuple[SungrowBinarySensorDescription, ...] = (
    _power_flow("pv_generating", PowerFlow.PV_GENERATING),
    _power_flow(
        "battery_charging",
        PowerFlow.BATTERY_CHARGING,
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
    ),
    _power_flow("battery_discharging", PowerFlow.BATTERY_DISCHARGING),
    _power_flow("positive_load_power", PowerFlow.LOAD_POSITIVE),
    _power_flow("exporting_power", PowerFlow.EXPORTING),
    _power_flow("importing_power", PowerFlow.IMPORTING),
    _power_flow("negative_load_power", PowerFlow.LOAD_NEGATIVE),
    SungrowBinarySensorDescription(
        key="grid_connected",
        translation_key="grid_connected",
        report_name="flows",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        is_on_fn=_grid_connected,
    ),
    SungrowBinarySensorDescription(
        key="inverter_alarm_active",
        translation_key="inverter_alarm_active",
        report_name="alarms",
        poll=SETTINGS,
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: d.alarms.any_active if d.alarms is not None else None,
        exists=lambda d: d.alarms is not None,
    ),
    SungrowBinarySensorDescription(
        key="apl_shutdown_at_zero",
        translation_key="apl_shutdown_at_zero",
        report_name="apl_shadow",
        poll=SETTINGS,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=lambda d: (
            d.apl_shadow.apl_shutdown_at_zero if d.apl_shadow is not None else None
        ),
        exists=lambda d: d.apl_shadow is not None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SungrowConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the binary sensors this inverter serves."""
    runtime = entry.runtime_data
    coordinators = {REALTIME: runtime.realtime, SETTINGS: runtime.settings}
    async_add_entities(
        SungrowBinarySensor(coordinators[description.poll], description)
        for description in BINARY_SENSORS
        if description.exists(runtime.device)
    )


class SungrowBinarySensor(SungrowEntity, BinarySensorEntity):
    """A flag read off the device."""

    entity_description: SungrowBinarySensorDescription

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.device)
