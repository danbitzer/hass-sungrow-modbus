"""Diagnostics: redaction and the register dump."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import HOST, SERIAL, setup_entry


async def test_diagnostics_redact_and_dump(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    config_entry: MockConfigEntry,
) -> None:
    await setup_entry(hass, config_entry)
    diagnostics = await get_diagnostics_for_config_entry(
        hass, hass_client, config_entry
    )
    text = str(diagnostics)
    assert SERIAL not in text
    assert HOST not in text
    assert diagnostics["entry"]["data"]["serial"] == "**REDACTED**"
    assert diagnostics["device"]["model"] == "SH15T"
    assert diagnostics["device"]["effective_battery_mode"] == "self_consumption"
    assert diagnostics["realtime"]["updated"]
    registers = diagnostics["registers"]
    assert registers["input"]["4999"] == 0x0E25
    assert registers["holding"]["13017"] == 0x55
    for address in range(4989, 4999):
        assert str(address) not in registers["input"]
