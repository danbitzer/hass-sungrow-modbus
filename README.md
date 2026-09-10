# hass-sungrow-modbus

A Home Assistant integration for **Sungrow SH-T hybrid inverters**
(SH5T–SH25T) built on Home Assistant's modernised Modbus support, plus the
standalone Python library it runs on.

> Status: under construction. See [PLAN.md](PLAN.md) for the design and
> milestones. Not yet installable.

## What's here

| Path | What |
|---|---|
| `packages/sungrow-inverter/` | `sungrow-inverter` on PyPI — the device library (no Home Assistant imports) |
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

## License

MIT. Register knowledge is derived from mkaiser's MIT-licensed package and
Sungrow's protocol document; see `NOTICE.md`.
