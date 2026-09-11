# Changelog

## Unreleased

- M3: `BatteryControl` — battery modes (self_consumption, no_charge, hold,
  forced_charge, forced_discharge), export limit and PV limitation as
  guarded writes: only registers that differ are written, in an order that
  leaves a sane state if one fails, read back and compared afterwards;
  `effective_mode()` reports what the settings imply; `scripts/control.py`
  drives it (plan-only without `--yes`). Verified live on an SH15T.
- M2: live read-only run on an SH15T (firmware P063, WiNet-S V300) — every
  value matched the mkaiser entities; readable ranges widened to the blocks
  the dongle proved it serves (a full sweep is now 24 reads instead of 36);
  the capture is committed as `tests/fixtures/sh15t_p063.json` with the
  serial replaced; the CLI names socket framing explicitly (the helper's
  default was RTU-over-TCP, which a WiNet-S never answers).
- M2 review fixes: mkaiser's undocumented meter phase registers dropped (a
  WiNet-S refuses them and answers 0 inside a wider block); documented backup
  voltages and frequency added to `Backup`; the M1 narrow map kept as a
  per-component fallback when a merged block is refused; `async_refresh`
  for a named subset of components; `battery_max_power_w` defaults to the
  lower of nominal and BDC power; `scripts/survey.py` committed; the CLI is
  TCP-only, never overwrites poll words in a dump and survives a hiccup on
  the setup re-reads; the fixture recaptured under the widened ranges with a
  test that every planned block address is present.
- Library: SH-T model gate, typed components for every polled register block
  (identity, firmware, AC/DC, flows, grid phases, meter, backup, battery,
  energy, alarms, settings, battery limits, start power, APL shadow, control),
  `SungrowInverter` with `async_probe`, realtime/settings updates and
  `UpdateReport`, `RetryingUnit` for WiNet-S exception-4 storms, and the
  read-only `scripts/query.py` (serial scrubbed from `--raw` dumps).
- Review fixes: S32 sentinel (0x7FFFFFFF → None) on load, export and meter
  powers so a missing meter cannot read as 2 GW; identity split into
  `Identity` (model gate) and optional `Ratings` so a refused ratings
  register cannot stop setup; undocumented meter phase registers moved to
  an optional `MeterPhases`; per-component refresh times and one-sweep raw
  collection for diagnostics; aa55 registers treat 0 as unserved; the
  no-real-serials guard also decodes register-word serials in JSON dumps;
  the CLI masks the serial and never sweeps the inverter twice; mypy strict
  now covers scripts, tests and the integration; library floor is 3.13.
- Repository skeleton: uv workspace with the `sungrow-inverter` library and
  the `sungrow` custom integration stub, CI, version sync, and the
  no-real-serials guard.
