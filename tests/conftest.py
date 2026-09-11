"""Integration test fixtures: a mock inverter behind Home Assistant's modbus."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from modbus_connection.mock import MockModbusConnection, MockModbusUnit
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sungrow.const import (
    CONF_BATTERY_MAX_POWER_W,
    CONF_BDC_RATED_POWER_W,
    CONF_DEVICE_TYPE_CODE,
    CONF_NOMINAL_POWER_W,
    CONF_SERIAL,
    CONF_UNIT_ID,
    DOMAIN,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = (
    ROOT
    / "packages"
    / "sungrow-sht-modbus"
    / "sungrow_inverter_tests"
    / "fixtures"
    / "sh15t_p063.json"
)

SERIAL = "A123456789"
HOST = "192.168.1.50"  # documentation example, allow-listed by the serial guard

CONNECTION = {CONF_HOST: HOST, CONF_PORT: 502, CONF_UNIT_ID: 1}
ENTRY_DATA = {
    **CONNECTION,
    CONF_DEVICE_TYPE_CODE: 0x0E25,
    CONF_SERIAL: SERIAL,
    CONF_NOMINAL_POWER_W: 15000,
    CONF_BDC_RATED_POWER_W: 30000,
}
ENTRY_OPTIONS = {CONF_BATTERY_MAX_POWER_W: 10000}


def load_raw() -> dict[str, dict[int, int | bool]]:
    data = json.loads(FIXTURE.read_text())
    return {
        space: {int(address): value for address, value in values.items()}
        for space, values in data.items()
    }


@pytest.fixture(autouse=True)
def _custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load custom_components/sungrow."""


@pytest.fixture
def mock_unit() -> MockModbusUnit:
    """The inverter: the live SH15T capture on the mock backend."""
    unit = MockModbusConnection().for_unit(1)
    unit.load_raw(load_raw())
    return unit


@pytest.fixture(autouse=True)
def _patch_modbus(mock_unit: MockModbusUnit) -> Generator[None]:
    """Hand the mock unit out where the integration asks modbus for one."""

    @asynccontextmanager
    async def temporary(*args: Any, **kwargs: Any) -> AsyncIterator[MockModbusUnit]:
        yield mock_unit

    with (
        patch("custom_components.sungrow.async_get_unit", return_value=mock_unit),
        patch(
            "custom_components.sungrow.config_flow.async_get_temporary_unit", temporary
        ),
    ):
        yield


@pytest.fixture
def config_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Sungrow SH15T",
        unique_id=SERIAL,
        data=ENTRY_DATA,
        options=ENTRY_OPTIONS,
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
