# hass-sungrow-modbus

A Home Assistant integration for **Sungrow SH-T hybrid inverters**
(SH5T–SH25T) built on Home Assistant's modernised Modbus support, plus the
standalone Python library it runs on.

> Status: under construction. See [PLAN.md](PLAN.md) for the design and
> milestones. Not yet installable.

## What's here

| Path | What |
|---|---|
| `packages/sungrow-sht-modbus/` | `sungrow-sht-modbus` on PyPI — the device library (no Home Assistant imports) |
| `custom_components/sungrow/` | The Home Assistant integration (HACS) |
| `PLAN.md` | Design, decisions, research and milestones |
| `CLAUDE.md` | Working rules for this repository |

## Why another Sungrow integration

The [mkaiser YAML package](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant)
reads and writes the registers well, but an automation driving it has to carry
its own "write only when the register differs", restore-before-switching and
power-before-mode logic. This integration owns that: actions such as
`sungrow.set_battery_mode` apply a desired state atomically, in the safe write
order, skipping writes that would change nothing, and verify by read-back.
It targets the SH-T family only.

## Development

```sh
uv sync                 # Python 3.14 workspace with Home Assistant test stack
scripts/check.sh        # ruff, mypy, pytest for both packages
python scripts/sync_version.py --check   # library version == manifest pin
```

Never put a real inverter serial number, host or address into anything git
tracks — see `CLAUDE.md`.

## Known register quirks (SH15T, firmware P063, WiNet-S V300)

- The daily grid counters (regs 13036 import, 13045 export) read 0 all day
  while the lifetime totals (13037, 13046) keep moving. Their sensors ship
  disabled; enable them if your firmware serves them.
- "Total output energy" (5004, documented as PV generation plus battery
  discharge) equals total export (13046) to the register, and its daily
  counterpart (5003) is below daily PV generation. It looks like the grid
  export counter on this firmware; the sensors keep the documented names.
- The WiNet-S answers 0 for a register it does not forward, so a
  self-consumption EMS mode is only believed when the running state agrees;
  otherwise `sensor.battery_mode` reads `inconsistent`.

## License

MIT. Register knowledge is derived from mkaiser's MIT-licensed package and
Sungrow's protocol document; see `NOTICE.md`.

## Installing

The integration needs the `sungrow-sht-modbus` library from PyPI (the manifest
pins the exact version) and Home Assistant 2026.9 or newer.

1. Copy `custom_components/sungrow` into your Home Assistant `config/custom_components/`
   (or add this repository as a custom repository in HACS once it is public).
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Sungrow SH-T Hybrid
   Inverter**. Enter the WiNet-S host, port 502 and unit id 1. The flow reads
   the identity block and refuses anything that is not an SH-T model.
4. Options: set **Battery max power** to the charge/discharge limit you run the
   battery at (the value self-consumption mode restores), not the inverter's
   rating.

The integration reads through Home Assistant's shared Modbus connection. If
the mkaiser YAML package is still loaded for the same dongle, expect
contention (exception 4) until it is removed; the library retries it.
