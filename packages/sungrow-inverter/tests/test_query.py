"""The query CLI's dump helpers: serial scrubbing and setup-block re-encoding."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
from modbus_connection.mock import MockModbusConnection, MockModbusUnit

from sungrow_inverter import SungrowInverter

from .conftest import SERIAL, text

SCRIPT = Path(__file__).parent.parent / "scripts" / "query.py"


@pytest.fixture(scope="module")
def query() -> ModuleType:
    spec = importlib.util.spec_from_file_location("query", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["query"] = module
    spec.loader.exec_module(module)
    return module


async def test_scrub_serial_replaces_all_ten_words(
    unit: MockModbusUnit, query: ModuleType
) -> None:
    unit.input[4989] = text("B999999999", 10)  # a stand-in for a real one
    raw = await SungrowInverter(unit).async_read_raw()
    query.scrub_serial(raw)
    assert [raw["input"][a] for a in range(4989, 4999)] == text(SERIAL, 10)
    replay = MockModbusConnection().for_unit(1)
    replay.load_raw(raw)
    inverter = SungrowInverter(replay)
    await inverter.async_update()
    assert inverter.identity.serial == SERIAL


async def test_setup_blocks_re_encode_to_the_words_read(
    unit: MockModbusUnit, query: ModuleType
) -> None:
    inverter = SungrowInverter(unit)
    report = await inverter.async_update(collect_raw=True)
    assert report.raw is not None
    merged = dict(report.raw)
    for name in ("identity", "ratings", "firmware"):
        merged = query._merge_setup_block(merged, getattr(inverter, name))
    assert merged == await inverter.async_read_raw()


def test_help_runs_without_a_backend() -> None:
    import subprocess

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--raw FILE" in result.stdout and "--show-serial" in result.stdout
