"""Helpers shared by setup and the config flow (no package-level imports)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.const import CONF_HOST, CONF_PORT
from modbus_connection import ModbusTcpParams

from .const import CONF_BDC_RATED_POWER_W, CONF_NOMINAL_POWER_W


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
