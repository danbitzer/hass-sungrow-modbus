# Changelog

## Unreleased

- **Fix (library 0.1.0a3): `set_export_limit` fought the inverter's own
  feed-in ratio mirror.** On the SH-T the feed-in ratio register (13088)
  and the value register (13074) are two views of one setting — writing
  either updates both. The routine treated a mirrored 0.3 % (= 50 W) as a
  ratio "below 100 %" that would override the watts, raised it to 100 %,
  and the inverter mirrored that back as 15000 W: every other five-minute
  re-assert of a 50 W cap lifted the cap for five minutes of export at
  negative feed-in, and the sweeps between failed verification with
  "settings.export_limit reads 15000 after writing 50" (live 2026-09-13).
  The ratio is now aligned to the target's own ratio, written before the
  watts (so the watts have the last word on a mirroring unit and both
  registers cap identically on one that doesn't), and only when it differs
  by more than the register's 0.1 % rounding; a re-assert of a cap that is
  already in place writes nothing.

- Entity naming pass (mkaiser parity dropped): "PV power" (was total DC
  power), "Inverter AC power", "Grid power" (signed export), "Lifetime …"
  for the lifetime counters, "Daily grid import/export", "Daily output
  energy" for registers 5003/5004, "Model", "… code" for the alarm words,
  "Load drawing/feeding power", "Shutdown at 0 % power limit"; the
  `sungrow_` key prefixes are gone and the control keys are `forced_power`,
  `charge_command`, `export_limit_enabled`, `pv_limitation`,
  `active_power_limit_enabled`. Unique ids of the renamed keys change:
  remove and re-add the integration to drop the orphans.
- Control calls run to their end even when the caller is cancelled (an
  automation in `mode: restart` cancels its in-flight service call when it
  is re-triggered): writes, read-back and publication all complete.
- `docs/numbat-migration.md`: the hand-over for reworking the Numbat
  actuator blueprint onto the actions, including the PV limitation
  capability and the open spike-power question.
- Live M5 follow-up: a read-back that fails after the writes landed (a
  WiNet-S sharing its link answered exception 4 three times in a row) no
  longer fails the action outright — the touched blocks are re-read and the
  plan is run once more; with nothing left to write the call counts as
  verified. The integration's unit retries four times instead of three.
- M5 review fixes (library 0.1.0a2): a failed write sequence drops the
  freshness of the components it touched, so the next guarded call re-reads
  before planning instead of trusting a cache that may not match the wire.
  In the integration, an action that fails after writing publishes only what
  the inverter answered: the library's read-back on a verification failure,
  otherwise a fresh read of the touched components, and if that read fails
  too the affected entities go unavailable rather than showing pre-write
  values as current. An unverified call re-reads only what it touched;
  start and stop no longer trigger a settings poll against an inverter that
  is booting or shutting down; a snapshot after a failed poll revives only
  what was actually read. A number or switch whose write succeeded but whose
  read-back failed says so ("written but could not be read back") and drops
  until the next poll. Enabling the active power limitation while its ratio
  reads 0 % is refused under "shutdown at 0 %", like the ratio itself. The
  stop report merges the restore's writes. Service descriptions say the
  export limit is refused outside the inverter's range and that `enabled`
  is ignored when the limit is empty. 13 more tests.
- M5: controls and actions. Actions `sungrow.set_battery_mode` (six
  requestable modes, `power_w`, `verify`), `set_export_limit` (`limit_w`,
  `enabled`; empty lifts the limitation), `set_pv_limitation`,
  `start_inverter` and `stop_inverter` (which puts the battery into
  self-consumption first, because the EMS mode survives a shutdown). Every
  action goes through the library's guarded write layer, trusts a settings
  snapshot from the last poll, returns the write report plus the resulting
  battery mode as a response, and publishes what it read back so
  `sensor.battery_mode` moves at once. Raw entities for parity with mkaiser:
  numbers (forced power, max charge/discharge power, min/max SoC, backup
  reserve, export power limit, active power limit ratio), selects (EMS mode,
  charge command), switches (export limit, backup mode, PV limitation, active
  power limitation) and buttons (start; stop disabled by default). Numbers
  range to what the inverter can move, not to the battery max power option;
  a 0 % active power limit is refused while "shutdown at 0 %" is on. Typed
  errors with translations. 16 tests.
- The library's PyPI name is `sungrow-sht-modbus` (PyPI refused
  `sungrow-inverter` as too similar to an existing project); the import
  name stays `sungrow_inverter`. The package directory follows.
- M4 review fixes: `BatteryMode.INCONSISTENT` replaces `unknown` (which
  collided with Home Assistant's own "no value" state); `effective_mode()`
  no longer logs — the settings coordinator warns once when the settings
  stop adding up and once when they recover; a settings poll that finds a
  control call holding the lock returns the last report instead of waiting
  on itself; diagnostics reads the registers under the control lock; an
  entity derived from several components (`grid_connected`,
  `battery_mode`) is unavailable when any of them failed; the 1 % dip
  filter also covers lifetime totals; connection errors count towards the
  stuck-link drop; the probe in the config flow retries once and never
  waits out a silent host; no `None` battery max power is stored; the
  writable limits are no longer mirrored as diagnostic sensors (they become
  number entities in M5); the device has an explicit name and no bogus
  hardware version; message spacing is cleared from the shared connection
  on unload; `PARALLEL_UPDATES` declared; the daily grid import/export
  counters ship disabled (they never move on an SH15T P063 while their
  totals do). 15 more integration tests (control lock held by the settings
  poll, link drop, restore across a restart, dip filter, no serial,
  options without a rating, dump toggle, mkaiser unit classes for every
  energy id).
- M4: the `sungrow` integration — config flow (host/port/unit, probed
  through `modbus`'s temporary unit, gated on the SH-T model, unique id =
  serial, reconfigure requires the same inverter), options (poll intervals,
  battery max power, request spacing, register dump), two coordinators
  (measurements every 10 s, settings every 60 s under the control lock),
  sensors for every measurement, energy counter (restored across restarts)
  and diagnostic mirror, binary sensors for the power-flow bits, grid
  presence and alarms, `sensor.battery_mode` with the settings as
  attributes, and diagnostics with the serial and host redacted. The
  library's test package is now `sungrow_inverter_tests` (two `tests`
  packages cannot share one pytest session).
- M3: `BatteryControl` — battery modes (self_consumption, no_charge, hold,
  forced_charge, forced_discharge), export limit and PV limitation as
  guarded writes: only registers that differ are written, in an order that
  leaves a sane state if one fails, read back and compared afterwards;
  `effective_mode()` reports what the settings imply; `scripts/control.py`
  drives it (plan-only without `--yes`). Verified live on an SH15T.
- M3 review fixes: reversing a running forced mode stops it before the new
  power; leaving a forced mode writes the EMS mode first; a doubted EMS word
  is written rather than skipped; fences accept any value at or below them;
  read-back re-reads every touched component; unanswered writes are
  `WriteUncertainError`; every control error after a write carries the
  report; `set_export_limit` raises an overriding feed-in ratio to 100 %,
  warns about an active power limitation and takes `enabled=`;
  `no_discharge` is requestable; `plan()` previews; per-call `max_age_s`.
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
