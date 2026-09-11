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


def test_help_runs_without_a_backend() -> None:
    import subprocess

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--raw FILE" in result.stdout and "--show-serial" in result.stdout


def test_tcp_defaults_to_socket_framing(query: ModuleType) -> None:
    """A WiNet-S speaks plain Modbus TCP; RTU-over-TCP never answers.

    Naming only the serial framer made the helper default tcp to rtu too.
    """
    import argparse

    from modbus_connection.cli_helper import add_connection_args

    parser = argparse.ArgumentParser()
    add_connection_args(parser, connections=query.CONNECTIONS)
    args = parser.parse_args(["host.invalid"])
    assert args.transport == "tcp"
    # No --framer given: connect_from_args passes none and the backend's
    # connect_tcp default applies, which must be socket framing.
    import inspect

    assert args.framer == "socket"
    with pytest.raises(SystemExit):
        parser.parse_args(["host.invalid", "--transport", "serial"])
    # The backend is only installed with the `cli` extra.
    tmodbus = pytest.importorskip("modbus_connection.tmodbus")
    assert inspect.signature(tmodbus.connect_tcp).parameters["framer"].default == (
        "socket"
    )
