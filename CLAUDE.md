# hass-sungrow-modbus — working rules

Two packages in one uv workspace: the `sungrow-inverter` device library
(`packages/sungrow-inverter/`, PyPI, **no Home Assistant imports**) and the
`sungrow` custom integration (`custom_components/sungrow/`). `PLAN.md` is the
design of record; follow its milestone order.

## Hard constraint: a real serial number, host or address is never published

This repository is public. Nothing git tracks may contain a real inverter
serial (shape: a letter followed by ten digits), a LAN address, a hostname, or
an exact capture time tied to a household — not fixtures, docstrings, comments,
commit messages, or diagnostics samples.

- Raw captures with real values go to `.testdata/` (gitignored) only.
- Test fixtures carry invented serials: `A123456789`, or `A987654321` where
  two must differ. `tests/test_no_real_serials.py` fails the suite on any
  other serial-shaped string; adding to its allow-list is a deliberate commit.
- Connection details for a real inverter come from the environment
  (`SUNGROW_HOST`, `SUNGROW_PORT`, `SUNGROW_UNIT`) or a gitignored `.env`.
- A stand-in serial is not consent: publish readings from someone else's
  installation only with their agreement.

## Hard constraint: entity migration must preserve history

Entity ids, unit classes and `state_class` of ported entities must match the
mkaiser YAML entries they replace; a wrong unit class freezes long-term
statistics flat while raw history keeps filling. See PLAN.md §2a.

## Versions

The library version (`packages/sungrow-inverter/pyproject.toml`) and the
manifest pin (`custom_components/sungrow/manifest.json` → `requirements`)
must be equal. Never edit either by hand:
`python scripts/sync_version.py --set X.Y.Z` moves both and re-installs the
editable package; `--check` is enforced in CI and in pytest.

## Conventions

- Addresses are Modbus **protocol addresses**, one below the register number
  in Sungrow's document. Every field docstring says `(reg N)`.
- 32-bit values are `word_order="little"` (Sungrow sends the low word first).
- The library never writes a register during setup; only explicit calls do.
- Never put `custom_components/` on `PYTHONPATH`.
- The integration never opens its own connection; it asks
  `homeassistant.components.modbus.async_get_unit` for a unit.

## Checks

`scripts/check.sh` runs ruff format/check, mypy (library, strict) and pytest
for both packages. Run it before committing. CI also runs hassfest and the
HACS validation.

## Changelog

Every change that affects the integration or the library adds an entry under
`## Unreleased` in `CHANGELOG.md`.
