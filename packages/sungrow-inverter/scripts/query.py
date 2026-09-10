"""Read an SH-T inverter once and print every value. Read-only.

    uv run --package sungrow-inverter python scripts/query.py "$SUNGROW_HOST" \
        --unit "${SUNGROW_UNIT:-1}" [--raw .testdata/raw.json]

The raw dump replaces the serial number with the stand-in ``A123456789``
unless ``--keep-serial`` is given, so it can be committed as a fixture.
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
    print_component,
)
from modbus_connection.encode import encode_string

from sungrow_inverter import (
    RetryingUnit,
    RetryPolicy,
    SungrowInverter,
    UnsupportedModelError,
)
from sungrow_inverter.components import Identity

STAND_IN_SERIAL = "A123456789"


def scrub_serial(raw: dict[str, dict[int, int | bool]]) -> None:
    """Replace the serial registers in a raw dump with the stand-in."""
    inputs = raw.get("input")
    if inputs is None:
        return
    field = Identity.declared_fields["serial"]
    words = encode_string(STAND_IN_SERIAL, length=field.count)
    for offset, word in enumerate(words):
        if field.address + offset in inputs:
            inputs[field.address + offset] = word


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Query a Sungrow SH-T inverter and print every value (read-only)."
    )
    add_connection_args(parser, connections=(("tcp", None), ("serial", "rtu")))
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
        help="also dump async_read_raw() as JSON to FILE (serial scrubbed)",
    )
    parser.add_argument(
        "--keep-serial",
        action="store_true",
        help="keep the real serial in the --raw dump (never commit such a file)",
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
    try:
        try:
            probe = await SungrowInverter.async_probe(unit)
        except UnsupportedModelError as err:
            print(f"Unsupported inverter: {err}", file=sys.stderr)
            return 2
        print(
            f"{probe.model.name} (0x{probe.device_type_code:04X}), "
            f"{probe.model.mppt} MPPT, nominal {probe.nominal_power_w} W, "
            f"BDC {probe.bdc_rated_power_w} W, protocol {probe.protocol_version}"
        )
        print()

        inverter = SungrowInverter(unit, model=probe.model)
        report = await inverter.async_update()
        for name in ("identity", "firmware", *inverter.polled_components):
            component = getattr(inverter, name)
            if component is None:
                continue
            print_component(component, title=name)
            print()
        raw = await inverter.async_read_raw() if args.raw else None
    finally:
        await conn.close()

    print(f"{counting.reads} Modbus reads, {unit.retries} retries")
    for name, err in report.failed.items():
        print(f"FAILED {name}: {err}")
    if raw is not None and args.raw is not None:
        if not args.keep_serial:
            scrub_serial(raw)
        args.raw.parent.mkdir(parents=True, exist_ok=True)
        args.raw.write_text(json.dumps(raw, indent=1) + "\n")
        print(f"raw dump written to {args.raw}")
    return 0 if report.ok else 3


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
