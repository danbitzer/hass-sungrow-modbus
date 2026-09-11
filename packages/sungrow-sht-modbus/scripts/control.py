"""Drive the guarded write layer from a terminal. WRITES to the inverter.

    uv run --package sungrow-sht-modbus python \\
        packages/sungrow-sht-modbus/scripts/control.py "$SUNGROW_HOST" \\
        --battery-max-power 10000 --yes \\
        apply self_consumption | apply no_charge | apply no_discharge | apply hold \\
        | apply forced_charge --power 2000 | apply forced_discharge --power 1000 \\
        | export-limit 0 | export-limit off | pv-limit on | pv-limit off \\
        | status

Without ``--yes`` every command only prints the plan (the writes it would
make against the settings it read) and exits. ``status`` never writes.
``start`` and ``stop`` are deliberately not offered here.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from modbus_connection import ModbusError
from modbus_connection.cli_helper import (
    CountingUnit,
    add_connection_args,
    connect_from_args,
)

from sungrow_inverter import (
    BatteryMode,
    ControlError,
    DesiredState,
    PlannedWrite,
    RetryingUnit,
    SungrowInverter,
    UnsupportedModelError,
)

CONNECTIONS = (("tcp", "socket"),)
MODES = [
    "self_consumption",
    "no_charge",
    "no_discharge",
    "hold",
    "forced_charge",
    "forced_discharge",
]


def _parse() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guarded writes to an SH-T inverter (plan only without --yes)."
    )
    add_connection_args(parser, connections=CONNECTIONS)
    parser.add_argument(
        "--unit", type=int, default=int(os.environ.get("SUNGROW_UNIT", "1"))
    )
    parser.add_argument(
        "--battery-max-power",
        type=int,
        metavar="W",
        help="the limit self_consumption restores; required for apply (the "
        "inverter's own rating may exceed what you run the battery at)",
    )
    parser.add_argument(
        "--yes", action="store_true", help="actually write; otherwise plan only"
    )
    parser.add_argument(
        "--no-verify", action="store_true", help="skip the read-back after writing"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    apply = sub.add_parser("apply", help="battery mode")
    apply.add_argument("mode", choices=MODES)
    apply.add_argument("--power", type=int, metavar="W", help="forced_* setpoint")
    export = sub.add_parser("export-limit", help="feed-in limitation")
    export.add_argument("limit", help="watts, or 'off'")
    pv = sub.add_parser("pv-limit", help="PV power limitation (reg 13018)")
    pv.add_argument("state", choices=["on", "off"])
    sub.add_parser("status", help="print the settings and the effective mode")
    args = parser.parse_args()
    if args.command == "apply" and args.battery_max_power is None:
        parser.error("apply needs --battery-max-power (the restore target)")
    return args


def _name(value: object) -> str:
    return getattr(value, "name", str(value)).lower()


def _print_settings(inverter: SungrowInverter) -> None:
    s, lim = inverter.settings, inverter.battery_limits
    print(
        f"  ems_mode={_name(s.ems_mode)} charge_command={_name(s.charge_command)} "
        f"forced_power={s.forced_power} W"
    )
    print(
        f"  max_charge={lim.max_charge_power} W "
        f"max_discharge={lim.max_discharge_power} W"
    )
    print(
        f"  export_limit={s.export_limit} W (enabled={s.export_limit_enabled}, "
        f"ratio={s.feed_in_ratio} %) pv_limitation={s.pv_power_limitation}"
    )
    print(
        f"  running_state={_name(inverter.flows.running_state)} "
        f"battery_power={inverter.battery_power.battery_power} W "
        f"level={inverter.battery.battery_level} %"
    )
    print(f"  effective mode: {inverter.effective_battery_mode}")


def _print_plan(planned: list[PlannedWrite]) -> None:
    print("plan (not written; pass --yes):")
    for item in planned:
        d = item.as_dict()
        note = "  (skip)" if d["skip"] else ""
        label = f"{d['component']}.{d['field']}"
        print(f"  {label}: {d['current']!r} -> {d['target']!r}{note}")


async def main() -> int:
    args = _parse()
    try:
        conn = await connect_from_args(args)
    except ModbusError as err:
        print(f"Could not connect: {err}", file=sys.stderr)
        return 1
    counting = CountingUnit(conn.for_unit(args.unit))
    unit = RetryingUnit(counting)
    try:
        inverter = SungrowInverter(unit, battery_max_power_w=args.battery_max_power)
        try:
            report = await inverter.async_update()
        except UnsupportedModelError as err:
            print(f"Unsupported inverter: {err}", file=sys.stderr)
            return 2
        except ModbusError as err:
            print(f"Read failed: {err}", file=sys.stderr)
            return 5
        if not report.ok:
            print(f"read failed for: {', '.join(report.failed)}", file=sys.stderr)
            return 3
        print("before:")
        _print_settings(inverter)
        control = inverter.battery_control
        verify = not args.no_verify

        if args.command == "status":
            return 0

        try:
            if args.command == "apply":
                desired = DesiredState(BatteryMode(args.mode), power_w=args.power)
                if not args.yes:
                    _print_plan(control.plan(desired))
                    return 0
                result = await control.apply(desired, verify=verify)
            elif args.command == "export-limit":
                limit = None if args.limit == "off" else int(args.limit)
                if not args.yes:
                    s = inverter.settings
                    print(
                        f"would set export limit to {limit!r} (now {s.export_limit} W, "
                        f"enabled={s.export_limit_enabled}, "
                        f"ratio={s.feed_in_ratio} %); pass --yes"
                    )
                    return 0
                result = await control.set_export_limit(limit, verify=verify)
            else:
                if not args.yes:
                    print(
                        f"would set pv limitation {args.state} "
                        f"(now {inverter.settings.pv_power_limitation}); pass --yes"
                    )
                    return 0
                result = await control.set_pv_limitation(
                    args.state == "on", verify=verify
                )
        except ControlError as err:
            print(f"FAILED: {err}", file=sys.stderr)
            if err.report is not None:
                print(json.dumps(err.report.as_dict(), indent=1), file=sys.stderr)
            return 4
        except ModbusError as err:
            print(f"Modbus error: {err}", file=sys.stderr)
            return 5
        print("result:")
        print(json.dumps(result.as_dict(), indent=1))
        # Fresh settings even with --no-verify or an all-skipped call.
        await inverter.async_refresh("settings", "battery_limits")
        await inverter.async_update_realtime()
        print("after:")
        _print_settings(inverter)
    finally:
        await conn.close()
    print(f"{counting.reads} Modbus reads, {unit.retries} retries")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
