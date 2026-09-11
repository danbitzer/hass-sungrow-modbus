"""Read an SH-T inverter once and print every value. Read-only.

    uv run --package sungrow-sht-modbus python scripts/query.py "$SUNGROW_HOST" \
        --unit "${SUNGROW_UNIT:-1}" [--raw .testdata/raw.json]

The serial number is masked on the terminal unless ``--show-serial`` is
given, and the raw dump replaces it with the stand-in ``A123456789`` unless
``--keep-serial`` is given, so the dump can be committed as a fixture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from modbus_connection import ModbusError
from modbus_connection.cli_helper import (
    CountingUnit,
    add_connection_args,
    connect_from_args,
    field_rows,
)
from modbus_connection.encode import encode_string
from modbus_connection.model import Component

from sungrow_inverter import (
    RetryingUnit,
    RetryPolicy,
    SungrowInverter,
    UnsupportedModelError,
)
from sungrow_inverter.components import Identity
from sungrow_inverter.report import Raw

STAND_IN_SERIAL = "A123456789"
MASK = "**********"
# Plain Modbus TCP only: a WiNet-S speaks socket framing and the Home Assistant
# integration never opens its own link, so no serial option is offered. Naming
# the framer matters: an unnamed one lets the helper pick another transport's
# default (RTU-over-TCP, which the dongle never answers).
CONNECTIONS = (("tcp", "socket"),)


def scrub_serial(raw: Raw) -> None:
    """Replace the serial registers in a raw dump with the stand-in."""
    inputs = raw.get("input")
    if inputs is None:
        return
    field = Identity.declared_fields["serial"]
    words = encode_string(STAND_IN_SERIAL, length=field.count)
    for offset, word in enumerate(words):
        if field.address + offset in inputs:
            inputs[field.address + offset] = word


def print_block(component: Component, title: str, *, mask: frozenset[str]) -> None:
    """Print a component's rows, masking the named fields."""
    rows = [
        (name, MASK if name in mask else value) for name, value in field_rows(component)
    ]
    print(title)
    print("-" * len(title))
    width = max((len(name) for name, _ in rows), default=0)
    for name, value in rows:
        print(f"  {name.ljust(width)}  {value}")
    print()


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query a Sungrow SH-T inverter and print every value (read-only)."
    )
    add_connection_args(parser, connections=CONNECTIONS)
    parser.add_argument(
        "--unit",
        type=int,
        default=int(os.environ.get("SUNGROW_UNIT", "1")),
        help="Modbus unit id (default: $SUNGROW_UNIT or 1)",
    )
    parser.add_argument(
        "--raw",
        type=Path,
        metavar="FILE",
        help="also dump the raw registers as JSON to FILE (serial scrubbed)",
    )
    parser.add_argument(
        "--keep-serial",
        action="store_true",
        help="keep the real serial in the --raw dump (never commit such a file)",
    )
    parser.add_argument(
        "--show-serial",
        action="store_true",
        help="print the real serial instead of masking it",
    )
    parser.add_argument(
        "--no-retry",
        action="store_true",
        help="do not retry transient Modbus failures (exception 4, timeouts)",
    )
    parser.add_argument(
        "--spacing",
        type=float,
        default=0.0,
        help="seconds between requests to the inverter (default 0)",
    )
    return parser.parse_args()


async def main() -> int:
    args = _parse()
    try:
        conn = await connect_from_args(args, message_spacing=args.spacing)
    except ModbusError as err:
        print(f"Could not connect: {err}", file=sys.stderr)
        return 1

    counting = CountingUnit(conn.for_unit(args.unit))
    policy = RetryPolicy(attempts=1) if args.no_retry else RetryPolicy()
    unit = RetryingUnit(counting, policy)
    mask = frozenset() if args.show_serial else frozenset({"serial"})
    try:
        inverter = SungrowInverter(unit)
        try:
            # One sweep: the poll also collects the raw words when asked.
            report = await inverter.async_update(collect_raw=bool(args.raw))
        except UnsupportedModelError as err:
            print(f"Unsupported inverter: {err}", file=sys.stderr)
            return 2
        except ModbusError as err:
            print(f"Read failed: {err}", file=sys.stderr)
            return 4
        model = inverter.model
        assert model is not None
        print(
            f"{model.name} (0x{model.code:04X}), {model.mppt} MPPT, "
            f"nominal {inverter.identity.nominal_power} W, "
            f"BDC {inverter.battery_max_power_w} W, "
            f"protocol {inverter.identity.protocol_version_text}"
        )
        print()
        for name in ("identity", "ratings", "firmware", *inverter.polled_components):
            component = getattr(inverter, name)
            if component is not None:
                print_block(component, name, mask=mask)
        # The setup blocks were read before the poll; three more reads put
        # their raw words into the dump too. A hiccup here must not lose the
        # sweep that already succeeded.
        setup_raw: list[Raw] = []
        if args.raw is not None:
            for name in ("identity", "ratings", "firmware"):
                component = getattr(inverter, name)
                if component is None:
                    continue
                try:
                    setup_raw.append(await component.async_read_raw(notify=False))
                except ModbusError as err:
                    print(f"WARNING {name} not in the dump: {err}", file=sys.stderr)
    finally:
        await conn.close()

    print(f"{counting.reads} Modbus reads, {unit.retries} retries")
    for name, failure in report.failed.items():
        print(f"FAILED {name}: {failure}")
    if args.raw is not None:
        raw: Raw = report.raw or {}
        for read in setup_raw:
            for space, values in read.items():
                # Never overwrite a poll word: the ratings block spans the
                # battery current at 5630, read seconds earlier by the poll.
                target = raw.setdefault(space, {})
                for address, word in values.items():
                    target.setdefault(address, word)
        raw = {space: dict(sorted(values.items())) for space, values in raw.items()}
        if not args.keep_serial:
            scrub_serial(raw)
        args.raw.parent.mkdir(parents=True, exist_ok=True)
        args.raw.write_text(json.dumps(raw, indent=1) + "\n")
        print(f"raw dump written to {args.raw}")
    return 0 if report.ok else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
