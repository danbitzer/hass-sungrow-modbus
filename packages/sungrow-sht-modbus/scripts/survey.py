"""Read-only block survey: which register spans does this inverter's link serve?

    uv run --package sungrow-sht-modbus python \
        packages/sungrow-sht-modbus/scripts/survey.py "$SUNGROW_HOST" \
        --unit "${SUNGROW_UNIT:-1}" [--repeat 3] [--probe input:5722:24 ...]

Prints one OK/FAIL line per probe, with the time each read took and, for a
probe of one or two registers, the words returned. Repeating a probe tells
contention (exception 4 that succeeds on a repeat) from refusal (exception 2
every time). The serial number registers are never printed. Nothing is
written.

Send this table when reporting a block the library reads that your dongle
refuses; it is how the readable ranges in ``sungrow_inverter.const`` were
established.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

from modbus_connection import ModbusError, ModbusUnit
from modbus_connection.cli_helper import add_connection_args, connect_from_args

from sungrow_inverter.const import HOLDING_RANGES, INPUT_RANGES

Probe = tuple[str, int, int, str]

SERIAL_ADDRESSES = range(4989, 4999)

# The library's own blocks (one probe per range), the widest reads a Modbus
# frame allows, and single reads on documented holes, which tell whether the
# link refuses a read that starts on an unforwarded address.
DEFAULT_PROBES: tuple[Probe, ...] = (
    *(
        (("input", low, high - low + 1, "library input range"))
        for low, high in INPUT_RANGES
    ),
    *(
        (("holding", low, min(high - low + 1, 125), "library holding range"))
        for low, high in HOLDING_RANGES
    ),
    ("input", 4951, 125, "widest input read"),
    ("input", 12999, 80, "running state ... alarms"),
    ("input", 5722, 24, "backup + meter phases"),
    ("input", 5740, 6, "meter phases alone (undocumented start)"),
    ("input", 5006, 1, "hole 5006"),
    ("input", 5008, 1, "hole 5008"),
    ("input", 13043, 1, "hole 13043"),
    ("input", 13047, 2, "hole 13047-13048"),
    ("holding", 13017, 83, "whole settings table"),
    ("holding", 13049, 3, "EMS mode, command, power"),
    ("holding", 13052, 1, "hole 13052"),
    ("holding", 13059, 1, "hole 13059"),
    ("holding", 33046, 2, "battery limits"),
    ("holding", 33148, 2, "start power"),
    ("holding", 12999, 1, "start/stop read-back"),
)


def parse_probe(text: str) -> Probe:
    """``space:address:count`` → probe."""
    try:
        space, address, count = text.split(":")
        if space not in ("input", "holding"):
            raise ValueError
        return (space, int(address), int(count), "user probe")
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not space:address:count with space input|holding"
        ) from None


async def run_probe(unit: ModbusUnit, probe: Probe) -> str:
    space, address, count, note = probe
    read = (
        unit.read_input_registers if space == "input" else unit.read_holding_registers
    )
    started = time.monotonic()
    try:
        words = await read(address, count)
    except ModbusError as err:
        took = time.monotonic() - started
        return (
            f"FAIL {space:7s} {address:5d} x{count:3d} {took:5.2f}s"
            f"  {note}: {type(err).__name__}: {err}"
        )
    took = time.monotonic() - started
    shown = ""
    serial = any(address + i in SERIAL_ADDRESSES for i in range(count))
    if count <= 2 and not serial:
        shown = " -> " + " ".join(f"0x{w:04X}" for w in words)
    return f"OK   {space:7s} {address:5d} x{count:3d} {took:5.2f}s  {note}{shown}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    add_connection_args(parser, connections=(("tcp", "socket"),))
    parser.add_argument(
        "--unit", type=int, default=int(os.environ.get("SUNGROW_UNIT", "1"))
    )
    parser.add_argument(
        "--repeat", type=int, default=1, help="reads per probe (default 1)"
    )
    parser.add_argument("--gap", type=float, default=0.3, help="seconds between reads")
    parser.add_argument(
        "--probe",
        action="append",
        type=parse_probe,
        default=[],
        metavar="SPACE:ADDRESS:COUNT",
        help="a probe to run instead of the default set; repeatable",
    )
    args = parser.parse_args()
    probes: tuple[Probe, ...] = tuple(args.probe) or DEFAULT_PROBES
    try:
        conn = await connect_from_args(args)
    except ModbusError as err:
        print(f"Could not connect: {err}", file=sys.stderr)
        return 1
    try:
        unit = conn.for_unit(args.unit)
        for probe in probes:
            for _ in range(args.repeat):
                print(await run_probe(unit, probe), flush=True)
                await asyncio.sleep(args.gap)
    finally:
        await conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
