"""Actions: the guarded control calls, addressed by device."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_get_device_and_config_entry

from sungrow_inverter import DESIRED_BATTERY_MODES, BatteryMode

from .const import (
    ATTR_ENABLED,
    ATTR_LIMIT,
    ATTR_LIMIT_W,
    ATTR_MODE,
    ATTR_POWER_W,
    ATTR_VERIFY,
    DOMAIN,
    SERVICE_SET_BATTERY_MODE,
    SERVICE_SET_EXPORT_LIMIT,
    SERVICE_SET_PV_LIMITATION,
    SERVICE_START_INVERTER,
    SERVICE_STOP_INVERTER,
)
from .control import (
    async_set_battery_mode,
    async_set_export_limit,
    async_set_pv_limitation,
    async_start_inverter,
    async_stop_inverter,
    response,
)

if TYPE_CHECKING:
    from . import SungrowRuntime

_DEVICE: dict[Any, Any] = {vol.Required(ATTR_DEVICE_ID): cv.string}
_VERIFY: dict[Any, Any] = {vol.Optional(ATTR_VERIFY, default=True): cv.boolean}

SET_BATTERY_MODE_SCHEMA = vol.Schema(
    {
        **_DEVICE,
        vol.Required(ATTR_MODE): vol.In(sorted(m.value for m in DESIRED_BATTERY_MODES)),
        vol.Optional(ATTR_POWER_W): vol.All(vol.Coerce(int), vol.Range(min=0)),
        **_VERIFY,
    }
)
SET_EXPORT_LIMIT_SCHEMA = vol.Schema(
    {
        **_DEVICE,
        vol.Optional(ATTR_LIMIT_W): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional(ATTR_ENABLED, default=True): cv.boolean,
        **_VERIFY,
    }
)
SET_PV_LIMITATION_SCHEMA = vol.Schema(
    {**_DEVICE, vol.Required(ATTR_LIMIT): cv.boolean, **_VERIFY}
)
DEVICE_SCHEMA = vol.Schema(_DEVICE)


def _runtime(call: ServiceCall) -> SungrowRuntime:
    _, entry = async_get_device_and_config_entry(
        call.hass, DOMAIN, call.data[ATTR_DEVICE_ID]
    )
    runtime: SungrowRuntime = entry.runtime_data
    return runtime


async def _set_battery_mode(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    mode = BatteryMode(call.data[ATTR_MODE])
    power = call.data.get(ATTR_POWER_W)
    if mode in (BatteryMode.FORCED_CHARGE, BatteryMode.FORCED_DISCHARGE) and (
        power is None
    ):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="power_required",
            translation_placeholders={"mode": mode.value},
        )
    report = await async_set_battery_mode(
        runtime, mode, power, verify=call.data[ATTR_VERIFY]
    )
    return response(runtime, report) if call.return_response else None


async def _set_export_limit(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    report = await async_set_export_limit(
        runtime,
        call.data.get(ATTR_LIMIT_W),
        enabled=call.data[ATTR_ENABLED],
        verify=call.data[ATTR_VERIFY],
    )
    return response(runtime, report) if call.return_response else None


async def _set_pv_limitation(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    report = await async_set_pv_limitation(
        runtime, call.data[ATTR_LIMIT], verify=call.data[ATTR_VERIFY]
    )
    return response(runtime, report) if call.return_response else None


async def _start_inverter(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    report = await async_start_inverter(runtime, call.context.user_id)
    return response(runtime, report) if call.return_response else None


async def _stop_inverter(call: ServiceCall) -> ServiceResponse:
    runtime = _runtime(call)
    report = await async_stop_inverter(runtime, call.context.user_id)
    return response(runtime, report) if call.return_response else None


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the actions once, at integration setup."""
    for name, handler, schema in (
        (SERVICE_SET_BATTERY_MODE, _set_battery_mode, SET_BATTERY_MODE_SCHEMA),
        (SERVICE_SET_EXPORT_LIMIT, _set_export_limit, SET_EXPORT_LIMIT_SCHEMA),
        (SERVICE_SET_PV_LIMITATION, _set_pv_limitation, SET_PV_LIMITATION_SCHEMA),
        (SERVICE_START_INVERTER, _start_inverter, DEVICE_SCHEMA),
        (SERVICE_STOP_INVERTER, _stop_inverter, DEVICE_SCHEMA),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=schema,
            supports_response=SupportsResponse.OPTIONAL,
        )
