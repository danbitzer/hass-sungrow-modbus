"""Spec-derived register seeds shaped like an SH15T, and helpers around them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from modbus_connection.cli_helper import CountingUnit
from modbus_connection.encode import encode_string
from modbus_connection.mock import MockModbusUnit

from sungrow_inverter import SungrowInverter

FIXTURES = Path(__file__).parent / "fixtures"

SERIAL = "A123456789"


def s16(value: int) -> int:
    return value & 0xFFFF


def u32(value: int) -> list[int]:
    """Two words, low word first (Sungrow's little word order)."""
    value &= 0xFFFF_FFFF
    return [value & 0xFFFF, value >> 16]


def text(value: str, count: int) -> list[int]:
    return encode_string(value, length=count)


def sh15t_input() -> dict[int, Any]:
    """Input registers of a three-phase SH15T charging from PV at midday."""
    seed: dict[int, Any] = {
        4951: u32(0x01010F00),  # protocol V1.1.15
        4953: text("PEARL-H_B000.V000.P099", 15),
        4968: text("PEARL-H_D000.V000.P099", 15),
        4989: text(SERIAL, 10),
        4999: 0x0E25,  # SH15T
        5000: 150,  # 15.0 kW nominal
        5001: 1,  # 3P4L
        5002: 123,  # 12.3 kWh today
        5003: u32(45678),  # 4567.8 kWh total
        5007: 412,  # 41.2 °C
        5010: [3800, 52, 3650, 48, 2900, 21],  # MPPT 1-3 V/A
        5016: u32(6250),  # total DC power
        5018: [2401, 2398, 2405],
        5032: u32(-150),  # reactive power
        5034: 998,  # PF 0.998
        5213: u32(-2500),  # battery power: charging
        5241: 5001,  # 50.01 Hz
        5600: u32(-1200),  # meter: exporting 1.2 kW
        5602: u32(-400),
        5604: u32(-400),
        5606: u32(-400),
        5621: 0,
        5622: 1500,  # 15000 W max feed-in
        5627: 150,  # BDC 15.0 kW
        5630: s16(-52),  # -5.2 A
        5634: 200,
        5635: 200,
        5638: 4480,  # 44.80 kWh
        5722: [0, 0, 0],
        5725: u32(0),
        5740: [2401, 2398, 2405],
        5743: [165, 170, 168],
        12999: 0x8100,  # derating running
        13000: 0b0001_1011,  # PV + charging + load + exporting
        13001: 251,
        13002: u32(123456),
        13004: 50,
        13005: u32(55555),
        13007: u32(1850),  # load
        13009: u32(1200),  # export
        13011: 80,
        13012: u32(4444),
        13016: 121,
        13017: u32(33333),
        13019: 5123,  # 512.3 V
        13020: 52,
        13021: 2500,
        13022: 655,  # 65.5 %
        13023: 1000,  # SoH 100.0 %
        13024: 250,  # 25.0 °C
        13025: 31,
        13026: u32(22222),
        13028: 850,  # 85.0 % self-consumption
        13030: [25, 24, 26],
        13033: u32(-1450),
        13035: 5,
        13036: u32(11111),
        13039: 90,
        13040: u32(4600),
        13044: 60,
        13045: u32(60000),
        13249: text("PEARL-H_B000.V000.P099", 15),
        13264: text("WINET-SV200.001.00.P020", 15),
        13279: text("SBRHV_B003.V003.P004", 15),
    }
    for address in range(13049, 13079, 2):
        seed[address] = u32(0)
    seed[13049] = u32(0x0001_0000)  # inverter alarm bit 16: SPD or fuse
    return seed


def sh15t_holding() -> dict[int, Any]:
    """Holding registers: self-consumption, export limit off, limits at 12 kW."""
    return {
        13017: 0x55,  # allow PV
        13049: 0,  # self-consumption
        13050: 0xCC,  # stop
        13051: 0,
        13057: 1000,  # max SoC 100.0 %
        13058: 50,  # min SoC 5.0 %
        13073: 15000,  # export limit W
        13074: 0xAA,  # backup on
        13086: 0x55,  # export limit off
        13087: 1000,  # feed-in ratio 100.0 %
        13088: 0x55,  # APL off
        13089: 1000,  # APL ratio 100.0 %
        13099: 20,  # backup reserve 20 %
        31212: 0xAA,  # APL shutdown at zero ON
        33046: 1200,  # 12000 W
        33047: 1200,
        33148: 7,  # 70 W
        33149: 7,
    }


@pytest.fixture
def unit(mock_modbus_unit: MockModbusUnit) -> MockModbusUnit:
    """A mock unit seeded like an SH15T."""
    mock_modbus_unit.input.update(sh15t_input())
    mock_modbus_unit.holding.update(sh15t_holding())
    return mock_modbus_unit


@pytest.fixture
def inverter(unit: MockModbusUnit) -> SungrowInverter:
    return SungrowInverter(unit)


def load_fixture(name: str) -> dict[str, dict[int, int | bool]]:
    """A committed ``async_read_raw()`` dump, with its JSON keys back to ints."""
    data = json.loads((FIXTURES / name).read_text())
    return {
        space: {int(address): value for address, value in values.items()}
        for space, values in data.items()
    }


class FlakyUnit(CountingUnit):
    """Fail the first ``failures`` matching reads/writes with ``error``, then delegate.

    Built on the framework's ``CountingUnit`` so it implements the whole
    ``ModbusUnit`` protocol; only the four register operations are wrapped.
    """

    def __init__(
        self,
        unit: MockModbusUnit,
        error: Exception,
        failures: int,
        *,
        address: int | None = None,
    ) -> None:
        super().__init__(unit)
        self._error = error
        self.failures_left = failures
        self._address = address
        self.calls = 0

    def _maybe_fail(self, address: int) -> None:
        self.calls += 1
        if self.failures_left and (self._address is None or self._address == address):
            self.failures_left -= 1
            raise self._error

    async def read_input_registers(self, address: int, count: int) -> list[int]:
        self._maybe_fail(address)
        return await super().read_input_registers(address, count)

    async def read_holding_registers(self, address: int, count: int) -> list[int]:
        self._maybe_fail(address)
        return await super().read_holding_registers(address, count)

    async def write_register(self, address: int, value: int) -> None:
        self._maybe_fail(address)
        await super().write_register(address, value)

    async def write_registers(self, address: int, values: list[int]) -> None:
        self._maybe_fail(address)
        await super().write_registers(address, values)
