"""The Sungrow SH-T hybrid inverter integration.

Reads and controls an SH-T inverter through Home Assistant's shared Modbus
connections and the ``sungrow-inverter`` library. The integration never opens
a connection itself; it asks ``modbus`` for a unit.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from modbus_connection import ModbusTcpParams

from sungrow_inverter import RetryingUnit, SungrowInverter

from .const import (
    CONF_BATTERY_MAX_POWER_W,
    CONF_BDC_RATED_POWER_W,
    CONF_MESSAGE_SPACING_MS,
    CONF_NOMINAL_POWER_W,
    CONF_REALTIME_INTERVAL,
    CONF_SETTINGS_INTERVAL,
    CONF_UNIT_ID,
    DEFAULT_MESSAGE_SPACING_MS,
    DEFAULT_REALTIME_INTERVAL,
    DEFAULT_SETTINGS_INTERVAL,
    DOMAIN,
    MIN_MESSAGE_SPACING_MS,
    PLATFORMS,
    REALTIME,
    SETTINGS,
)
from .coordinator import SungrowCoordinator

__all__ = ["DOMAIN", "SungrowConfigEntry", "SungrowRuntime", "create_modbus_params"]


@dataclass
class SungrowRuntime:
    """What a loaded entry holds."""

    device: SungrowInverter
    realtime: SungrowCoordinator
    settings: SungrowCoordinator


type SungrowConfigEntry = ConfigEntry[SungrowRuntime]


def create_modbus_params(data: Mapping[str, Any]) -> ModbusTcpParams:
    """The connection parameters an entry's data describes (Modbus TCP)."""
    return ModbusTcpParams(host=str(data[CONF_HOST]), port=int(data[CONF_PORT]))


def battery_max_power_default(data: Mapping[str, Any]) -> int | None:
    """The restore target when the option is unset: min(nominal, BDC)."""
    known = [
        int(data[key])
        for key in (CONF_NOMINAL_POWER_W, CONF_BDC_RATED_POWER_W)
        if data.get(key) is not None
    ]
    return min(known) if known else None


async def async_setup_entry(hass: HomeAssistant, entry: SungrowConfigEntry) -> bool:
    """Set up an inverter from a config entry."""
    try:
        unit = async_get_unit(
            hass, entry, create_modbus_params(entry.data), int(entry.data[CONF_UNIT_ID])
        )
    except HomeAssistantError as err:
        # The device is in use over different link settings.
        raise ConfigEntryNotReady(str(err)) from err

    spacing_ms = max(
        int(entry.options.get(CONF_MESSAGE_SPACING_MS, DEFAULT_MESSAGE_SPACING_MS)),
        MIN_MESSAGE_SPACING_MS,
    )
    unit.set_message_spacing(spacing_ms / 1000)

    battery_max_power = entry.options.get(CONF_BATTERY_MAX_POWER_W)
    device = SungrowInverter(
        RetryingUnit(unit),
        battery_max_power_w=(
            int(battery_max_power)
            if battery_max_power is not None
            else battery_max_power_default(entry.data)
        ),
    )
    realtime = SungrowCoordinator(
        hass,
        entry,
        device,
        REALTIME,
        device.async_update_realtime,
        timedelta(
            seconds=int(
                entry.options.get(CONF_REALTIME_INTERVAL, DEFAULT_REALTIME_INTERVAL)
            )
        ),
        count_timeouts=True,
    )
    settings = SungrowCoordinator(
        hass,
        entry,
        device,
        SETTINGS,
        device.async_update_settings,
        timedelta(
            seconds=int(
                entry.options.get(CONF_SETTINGS_INTERVAL, DEFAULT_SETTINGS_INTERVAL)
            )
        ),
        # A settings poll must not interleave with an action's write sequence.
        lock=device.battery_control.lock,
    )
    # The first realtime refresh runs the library's setup: identity, model
    # gate, optional probes. Nothing is written.
    await realtime.async_config_entry_first_refresh()
    await settings.async_config_entry_first_refresh()

    entry.runtime_data = SungrowRuntime(device, realtime, settings)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SungrowConfigEntry) -> bool:
    """Unload an entry; ``modbus`` closes the connection when nothing holds it."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
