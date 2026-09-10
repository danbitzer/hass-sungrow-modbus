# Changelog

## Unreleased

- Library: SH-T model gate, typed components for every polled register block
  (identity, firmware, AC/DC, flows, grid phases, meter, backup, battery,
  energy, alarms, settings, battery limits, start power, APL shadow, control),
  `SungrowInverter` with `async_probe`, realtime/settings updates and
  `UpdateReport`, `RetryingUnit` for WiNet-S exception-4 storms, and the
  read-only `scripts/query.py` (serial scrubbed from `--raw` dumps).
- Repository skeleton: uv workspace with the `sungrow-inverter` library and
  the `sungrow` custom integration stub, CI, version sync, and the
  no-real-serials guard.
