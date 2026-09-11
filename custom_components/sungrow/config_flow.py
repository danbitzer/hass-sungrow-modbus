"""Config flow: connection details, probed against the inverter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.modbus import async_get_temporary_unit
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
)
from modbus_connection import ModbusError

from sungrow_inverter import (
    ProbeResult,
    RetryingUnit,
    RetryPolicy,
    SungrowInverter,
    UnsupportedModelError,
)

from .const import (
    CONF_BATTERY_MAX_POWER_W,
    CONF_BDC_RATED_POWER_W,
    CONF_DEVICE_TYPE_CODE,
    CONF_INCLUDE_REGISTER_DUMP,
    CONF_MESSAGE_SPACING_MS,
    CONF_NOMINAL_POWER_W,
    CONF_REALTIME_INTERVAL,
    CONF_SERIAL,
    CONF_SETTINGS_INTERVAL,
    CONF_UNIT_ID,
    DEFAULT_MESSAGE_SPACING_MS,
    DEFAULT_PORT,
    DEFAULT_REALTIME_INTERVAL,
    DEFAULT_SETTINGS_INTERVAL,
    DEFAULT_UNIT_ID,
    DOMAIN,
    MIN_MESSAGE_SPACING_MS,
)
from .helpers import battery_max_power_default, create_modbus_params

PROBE_RETRY = RetryPolicy(attempts=2, retry_timeouts=False)
"""One retry on a transient exception; a silent host fails within a timeout."""


def _box(
    low: float, high: float, step: float = 1, unit: str | None = None
) -> NumberSelector:
    config = NumberSelectorConfig(
        min=low, max=high, step=step, mode=NumberSelectorMode.BOX
    )
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, vol.UNDEFINED)
            ): TextSelector(),
            vol.Required(
                CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
            ): _box(1, 65535),
            vol.Required(
                CONF_UNIT_ID, default=defaults.get(CONF_UNIT_ID, DEFAULT_UNIT_ID)
            ): _box(1, 247),
        }
    )


def _normalise(user_input: Mapping[str, Any]) -> dict[str, Any]:
    return {
        CONF_HOST: str(user_input[CONF_HOST]).strip(),
        CONF_PORT: int(user_input[CONF_PORT]),
        CONF_UNIT_ID: int(user_input[CONF_UNIT_ID]),
    }


async def _async_probe(hass: HomeAssistant, data: Mapping[str, Any]) -> ProbeResult:
    """Read the identity block over a temporary unit; gates on the model."""
    async with async_get_temporary_unit(
        hass, create_modbus_params(data), int(data[CONF_UNIT_ID])
    ) as unit:
        return await SungrowInverter.async_probe(RetryingUnit(unit, PROBE_RETRY))


def _entry_data(connection: Mapping[str, Any], probe: ProbeResult) -> dict[str, Any]:
    return {
        **connection,
        CONF_DEVICE_TYPE_CODE: probe.device_type_code,
        CONF_SERIAL: probe.serial,
        CONF_NOMINAL_POWER_W: probe.nominal_power_w,
        CONF_BDC_RATED_POWER_W: probe.bdc_rated_power_w,
    }


class SungrowConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for a Sungrow SH-T inverter."""

    VERSION = 1

    async def _async_try_probe(
        self, data: Mapping[str, Any]
    ) -> tuple[ProbeResult | None, dict[str, str], dict[str, str]]:
        """Probe; returns (result, errors, placeholders)."""
        try:
            probe = await _async_probe(self.hass, data)
        except UnsupportedModelError as err:
            return (
                None,
                {"base": "unsupported_model"},
                {"model": err.known_name or "unknown", "code": f"0x{err.code:04X}"},
            )
        except (ModbusError, HomeAssistantError):
            return None, {"base": "cannot_connect"}, {}
        if probe.serial is None:
            return None, {"base": "no_serial"}, {}
        return probe, {}, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the connection and probe it."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            connection = _normalise(user_input)
            probe, errors, placeholders = await self._async_try_probe(connection)
            if probe is not None:
                await self.async_set_unique_id(probe.serial)
                self._abort_if_unique_id_configured(updates=connection)
                data = _entry_data(connection, probe)
                default = battery_max_power_default(data)
                return self.async_create_entry(
                    title=f"Sungrow {probe.model.name}",
                    data=data,
                    options=(
                        {CONF_BATTERY_MAX_POWER_W: default}
                        if default is not None
                        else {}
                    ),
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input or {}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the connection of an existing entry; must be the same inverter."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            connection = _normalise(user_input)
            probe, errors, placeholders = await self._async_try_probe(connection)
            if probe is not None:
                await self.async_set_unique_id(probe.serial)
                self._abort_if_unique_id_mismatch(reason="wrong_device")
                return self.async_update_reload_and_abort(
                    entry, data_updates=_entry_data(connection, probe)
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or entry.data),
            errors=errors,
            description_placeholders=placeholders,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> SungrowOptionsFlow:
        return SungrowOptionsFlow()


class SungrowOptionsFlow(OptionsFlowWithReload):
    """Polling intervals, the battery power to restore, link pacing."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={
                    CONF_REALTIME_INTERVAL: int(user_input[CONF_REALTIME_INTERVAL]),
                    CONF_SETTINGS_INTERVAL: int(user_input[CONF_SETTINGS_INTERVAL]),
                    CONF_BATTERY_MAX_POWER_W: int(user_input[CONF_BATTERY_MAX_POWER_W]),
                    CONF_MESSAGE_SPACING_MS: int(user_input[CONF_MESSAGE_SPACING_MS]),
                    CONF_INCLUDE_REGISTER_DUMP: bool(
                        user_input[CONF_INCLUDE_REGISTER_DUMP]
                    ),
                }
            )
        entry = self.config_entry
        options = entry.options
        max_power = battery_max_power_default(entry.data) or 10 * 0xFFFF
        current_power = options.get(CONF_BATTERY_MAX_POWER_W) or max_power
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_REALTIME_INTERVAL,
                    default=options.get(
                        CONF_REALTIME_INTERVAL, DEFAULT_REALTIME_INTERVAL
                    ),
                ): _box(5, 60, 1, "s"),
                vol.Required(
                    CONF_SETTINGS_INTERVAL,
                    default=options.get(
                        CONF_SETTINGS_INTERVAL, DEFAULT_SETTINGS_INTERVAL
                    ),
                ): _box(30, 600, 1, "s"),
                vol.Required(
                    CONF_BATTERY_MAX_POWER_W,
                    default=current_power,
                ): _box(10, max_power, 10, "W"),
                vol.Required(
                    CONF_MESSAGE_SPACING_MS,
                    default=options.get(
                        CONF_MESSAGE_SPACING_MS, DEFAULT_MESSAGE_SPACING_MS
                    ),
                ): _box(MIN_MESSAGE_SPACING_MS, 1000, 1, "ms"),
                vol.Required(
                    CONF_INCLUDE_REGISTER_DUMP,
                    default=options.get(CONF_INCLUDE_REGISTER_DUMP, True),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
