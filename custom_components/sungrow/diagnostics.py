"""Diagnostics: the entry, both poll reports, and the raw register map."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from modbus_connection import ModbusError

from sungrow_inverter import UpdateReport

from . import SungrowConfigEntry
from .const import CONF_INCLUDE_REGISTER_DUMP, CONF_SERIAL, SERIAL_ADDRESS, SERIAL_WORDS

TO_REDACT = {CONF_HOST, CONF_SERIAL}


def _report(report: UpdateReport | None) -> dict[str, Any] | None:
    if report is None:
        return None
    return {
        "updated": list(report.updated),
        "failed": {name: str(err) for name, err in report.failed.items()},
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SungrowConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    device = runtime.device
    model = device.model
    data: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "device": {
            "model": model.name if model is not None else None,
            "mppt": model.mppt if model is not None else None,
            "protocol_version": device.identity.protocol_version_text,
            "firmware": (
                None
                if device.firmware is None
                else {
                    "inverter": device.firmware.inverter_firmware,
                    "comm_module": device.firmware.comm_module_firmware,
                    "battery": device.firmware.battery_firmware,
                }
            ),
            "optional_components": {
                name: getattr(device, name) is not None
                for name in (
                    "ratings",
                    "firmware",
                    "start_power",
                    "apl_shadow",
                    "alarms",
                )
            },
            "battery_max_power_w": device.battery_max_power_w,
            "effective_battery_mode": (
                device.effective_battery_mode.value
                if device.effective_battery_mode is not None
                else None
            ),
        },
        "realtime": _report(runtime.realtime.data),
        "settings": _report(runtime.settings.data),
    }
    if entry.options.get(CONF_INCLUDE_REGISTER_DUMP, True):
        try:
            # A raw read also refreshes the device's stored values, which a
            # control call in progress relies on: take its lock.
            async with device.battery_control.lock:
                registers = await device.async_read_raw()
        except ModbusError as err:
            data["registers"] = {"error": str(err)}
        else:
            inputs = registers.get("input", {})
            for address in range(SERIAL_ADDRESS, SERIAL_ADDRESS + SERIAL_WORDS):
                inputs.pop(address, None)
            data["registers"] = {
                space: {str(address): value for address, value in values.items()}
                for space, values in registers.items()
            }
    return data
