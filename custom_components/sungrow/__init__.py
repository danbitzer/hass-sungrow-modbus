"""The Sungrow SH-T hybrid inverter integration.

Reads and controls an SH-T inverter through Home Assistant's shared Modbus
connections and the ``sungrow-sht-modbus`` library. The integration never opens
a connection itself; it asks ``modbus`` for a unit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from homeassistant.components.modbus import async_get_unit
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError

from sungrow_inverter import RetryingUnit, SungrowInverter

from .const import (
    CONF_BATTERY_MAX_POWER_W,
    CONF_MESSAGE_SPACING_MS,
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
from .helpers import battery_max_power_default, create_modbus_params

__all__ = ["DOMAIN", "SungrowConfigEntry", "SungrowRuntime"]


@dataclass
class SungrowRuntime:
    """What a loaded entry holds."""

    device: SungrowInverter
    realtime: SungrowCoordinator
    settings: SungrowCoordinator


type SungrowConfigEntry = ConfigEntry[SungrowRuntime]


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
    # The spacing lives on the shared connection, which may outlive this entry.
    entry.async_on_unload(lambda: unit.set_message_spacing(0))

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
        # Only this tier recycles a stuck link: it polls six times as often
        # and shares the connection, so a wedged settings poll surfaces here.
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
        watch_battery_mode=True,
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
