# hass-sungrow-modbus — implementation plan

Status: 2026-09-11 — research complete; **M0 done** (skeleton, CI, guards); **M1 done** (library, reviewed and fixed); **M2 done and reviewed** (live read-only run on the SH15T: every modelled value matched the mkaiser entities; ranges widened per the WiNet-S block survey with the M1 map kept as a per-component fallback; capture in `tests/fixtures/sh15t_p063.json`; `scripts/survey.py`). Next: M3 `BatteryControl` (`inverter.battery_control`).
Repository is private for now, so the HACS validation job is advisory
(`continue-on-error`) until it is made public.

## 1. Context

Numbat (the battery optimizer add-on) never writes to the inverter. A user-owned
HA automation built from `blueprints/numbat_actuator.yaml` does, through the
mkaiser Sungrow Modbus YAML package. Over the last releases that automation has
accreted logic the YAML package cannot provide: every write is wrapped in a
"skip if the register already matches" guard, battery limits are restored before
every branch (but not while the branch needs them), the forced-power register is
written before the mode register, and a 5-minute sweep re-asserts the current
state. The Sungrow DOCS example is now ~100 lines of guarded YAML per user.

Home Assistant 2026.9 modernised Modbus: core's `modbus` integration hands out
shared `ModbusUnit`s (`homeassistant.components.modbus.async_get_unit`) built on
the `modbus-connection` library, which also ships a declarative device-modelling
framework and an in-memory mock backend for tests. Device integrations are now
expected to be a thin layer over a standalone PyPI device library.

This project builds that: a `sungrow-inverter` device library plus a `sungrow`
custom integration focused on the SH-T hybrid family (SH5T–SH25T), with the
guarded, ordered, atomic "desired state" operations built in so the Numbat
blueprint collapses to one action call per branch.

## 2. Decisions (agreed 2026-09-11)

| Decision | Choice |
|---|---|
| Positioning | Own project, MIT. Register map borrowed from mkaiser's YAML (MIT, credited) and Sungrow protocol V1.1.15. Not contributing to mkaiser's `proper-ha-integration` branch. |
| Repo layout | Monorepo: `packages/sungrow-inverter/` (PyPI library, no HA imports) + `custom_components/sungrow/` (integration requiring the PyPI library). uv workspace. |
| Names | HA domain `sungrow`; PyPI `sungrow-inverter`; import `sungrow_inverter`. (`sungrow-modbus` on PyPI is mkaiser's alpha; `pysungrow` is taken.) |
| Scope | SH-T only (device type codes 0x0E20–0x0E28); refuse other families in the config flow with a clear error. TCP first; serial params if cheap. |
| Control surface | HA actions for composite desired states (guarded, ordered, atomic) + raw number/select/switch entities + read-only `sensor.battery_mode`. |
| Failsafe | Heartbeat/grid-outage failsafe and the sweep stay in the Numbat blueprint (Numbat-specific); the integration is Numbat-agnostic. |

## 2a. Hard constraints (apply from the first commit)

These go into the repo's own `CLAUDE.md` in M0 and are enforced by tests/CI,
not by care. Both are lifted from mkaiser's `CLAUDE.md`, which learned them
the hard way.

### A real serial number, host or address is never published

This repo is public. Nothing tracked by git may contain a real inverter serial
(shape: letter + 10 digits, e.g. `A2…`), a LAN address, a hostname, or an
exact capture time tied to a household — not fixtures, docstrings, comments,
commit messages, the plan, or diagnostics samples.

- `.testdata/` is gitignored; raw captures with real values go there only.
- `scripts/query.py --raw` writes the test fixture with the serial block
  (input 4989–4998) replaced by an invented serial (`A123456789`), never
  records the host, and rounds the capture time to the day.
- Diagnostics download pops the serial block and redacts host/unique_id.
- `tests/test_no_real_serials.py` scans every tracked file for the serial
  shape and fails unless each hit is on an allow-list of invented values
  (`A123456789`, `A987654321`). Adding to the list is a deliberate commit.
- Connection details for Dan's inverter live in the environment
  (`SUNGROW_HOST`, `SUNGROW_UNIT`) or a gitignored `.env`, never in the repo.
  This plan uses `$SUNGROW_HOST` for the same reason.
- A stand-in serial is not consent: readings from anyone else's installation
  are published only with the owner's agreement.

### Migration must preserve history

Decided 2026-09-11: `reactive_power` keeps the spec unit `var` (mkaiser's entity says `W`); it is a new entity, not a port — a wrong unit class is worse than a fresh history. `battery_soh` and `self_consumption_today` need the same per-entity check at M4 (mkaiser rounds both to 0 decimals).

Numbat's config, its dashboard and 175 days of long-term statistics are keyed
by the mkaiser entity ids (`sensor.battery_level`, `sensor.battery_power`,
`sensor.load_power`, …). Facts that decide the cut-over procedure:

- A registry rename carries raw history and long-term statistics, and a
  `total_increasing` sum keeps climbing across it.
- A rename **onto an id the recorder already knows is refused** with only a
  log line — exactly the YAML-package case. So the legacy id must be free in
  the registry and the state machine *before* the new entity claims it (else
  the registry silently appends `_2`), and the claim should happen before the
  new entity has statistics of its own.
- Every ported entity keeps the **unit class and `state_class`** of the entry
  it replaces (W / % / kWh; `measurement` / `total_increasing` / `total`).
  Get the unit class wrong and long-term statistics freeze flat while raw
  history keeps filling, so nothing looks broken. Check per entity.
- Both directions stay reversible; nothing is copied or deleted.

## 3. Research findings that shape the design

### 3.1 The framework (verified against HA 2026.9.1 source)

- `homeassistant/components/modbus/connection.py` (present in 2026.9.1):
  `async_get_unit(hass, entry, params, unit_id) -> ModbusUnit` shares one
  connection per endpoint and closes it when the last entry unloads;
  `async_get_temporary_unit(hass, params, unit_id)` is the config-flow probe.
  Manifest needs `"dependencies": ["modbus"]`. Core pins
  `modbus-connection[tmodbus]==4.10.0` (dev: 4.11.1) → library pins `>=4.10,<5`.
- `modbus_connection.model`: `Component` subclasses declare fields
  (`integer/gauge/uint32/int32/string/enum/boolean/raw_register`, `nan=`
  sentinels, `word_order="little"` = YAML `swap: word`, `writable=True|validator`);
  `register_space = "input"|"holding"` per class; block reads are pooled
  (`max_gap` 16, `max_span` 125, `register_ranges` to fence reads); an update
  either applies fully or raises, so optional/fragile registers go in their own
  component; `ComponentGroup` pools several components; `async_read_raw()` feeds
  diagnostics; `Component.write(field, value)` encodes/scales and picks FC06/FC16.
  No read-back verification is built in — that is ours to add.
- Reference implementations: `Tom-Bom-badil/trovis-modbus` (library: hatchling,
  `version = "0.0.0"` patched from the release tag, CI = ruff format/check,
  compileall, pytest, build; publish via trusted publishing) and
  `trovis-modbus-hass` (HACS integration: `hacs.json` with `homeassistant:
  "2026.09.0"`, manifest `requirements: ["trovis-modbus>=3.0.0,<4"]`,
  coordinator returning the device, entities reading typed attributes,
  `async_refresh()` after writes). Ignore the core `trovis557x-integration`
  branch — it imports a `modbus_connection` component that does not exist in
  2026.9.
- Testing: the library tests use modbus-connection's pytest plugin
  (`mock_modbus_unit` with `holding`/`input` stores, `on_write`, `fail_read`,
  `read_events`); the integration uses
  `pytest-homeassistant-custom-component` (0.13.364 pins HA 2026.9.1).

### 3.2 Lessons from mkaiser (YAML package + `proper-ha-integration` branch)

Their branch already targets this exact framework (`async_get_unit`,
modbus-connection ≥4.10), so its measured findings transfer directly:

- **Connection scarcity** dominates every Sungrow support thread: the inverter
  accepts very few Modbus sessions; two clients produce "Connection lost before
  response was received". Running the YAML package and a new integration at once
  will fail — cut over, don't overlap. (Dan's own probe from the Mac reproduced
  this as intermittent exception 4 on 13xxx holding reads while HA polled.)
- **Blast radius of pooled reads**: one refused register fails its whole
  component. Registers to isolate on SHT: the three 15-register firmware strings
  at 13250/13265/13280 (some firmwares close the TCP connection on them),
  `battery_power` 5214, `apl_shutdown_at_zero` 31213, meter channel 2.
- **WiNet-S substitutes 0 for 0xFFFF** on absent registers → "all zeros" must be
  read as absent, not present (their `ZERO_MEANS_ABSENT`).
- **Writable registers must not be polled frequently through a WiNet-S**
  (spec note) → settings tier ≥ 5 s, default slower.
- Export limit 13074 is **1 W/count on SH-T** (the `/10` in older YAML was an
  RT quirk — issue #616 broke SH15T; do not port it). Bounds come from input
  registers 5622/5623 (10 W/count). Max charge/discharge power 33047/33048 are
  0.01 kW per spec but 10 W/count in practice (YAML `scale: 10`). Forced power
  13052 is 1 W/count.
- Start/stop (holding 13000, 0xCF/0xCE) is an admin-gated **action**, not a
  button (no confirmation dialog on buttons). 13000 is in both register tables:
  input = running state, holding = start/stop.
- Their integration has **no** write-if-different guard, no ordering, no
  read-back comparison, no re-assert — the gap this project fills. It does two
  things right that we copy: `async_refresh()` (never `async_request_refresh`,
  whose 10 s debounce hides a second write) and refreshing the *input* tier
  after a start/stop.
- Device families by type code: T = 0x0E20–0x0E28 (0x0E27 unused; SH15T =
  0x0E25 = 3621). SH-T has MPPT3 (5015/5016) and no MPPT4; three-phase.

### 3.3 SH-T register facts (spec V1.1.15 + live probes on Dan's SH15T, P063)

- PV power limitation **reg 13018** (address 13017): `0xAA` limit / `0x55`
  allow, RW, "Only SHT are supported". Reads 0x55 on Dan's unit and accepted a
  write (iSolarCloud shadow 31211 mirrored it). This is the negative-price
  "PV off" the blueprint needs.
- Active power limitation 13089 (enable) / 13090 (ratio 0.1 %): control point is
  the inverter AC port. Shadow 31213 "shutdown at 0 %" reads 0xAA (ON) on Dan's
  unit — never write ratio 0 without clearing it.
- EMS mode 13050 (0 self-consumption, 2 forced, 3 external EMS, 4 VPP);
  charge/discharge command 13051 (0xAA charge, 0xBB discharge, 0xCC stop);
  forced power 13052 (W); max/min SoC 13058/13059 (0.1 %); backup reserve 13100
  (%); off-grid/backup option 13075 (0xAA/0x55); feed-in limitation enable
  13087 (0xAA/0x55) + value 13074 (W); max charge/discharge power 33047/33048.
- Running state (input 13000) decodes with mkaiser's map, e.g. 0x8100 Derating
  Running, 0x8200 Dispatch Running, 0x1300 Key stop, 0x1400 Standby, 0x1600
  Starting. Power-flow status bits (input 13001): PV generating, battery
  charging/discharging, positive load, exporting, importing, negative load.
- Identity block (input 4952–5002): protocol version, ARM/DSP strings, serial
  (4990, 10 regs), device type (5000), rated output (5001, ×100 W), output type
  (5002). Firmware strings at 13250+ (SH-T supported).

### 3.3a WiNet-S block survey (M2, 2026-09-11, SH15T P063, WiNet-S firmware V300)

Read-only, from the Mac, while the mkaiser YAML hub kept polling:

- **Input reads answer any width up to 125** when the block starts on a
  documented register: 4951×125, 12999×80, 5010×25 (across the 5021-5031
  hole), 5007×14, 5600×39, 5722×24, 13249×45 all OK in 10-40 ms. Reserved
  holes *inside* a block read 0. A read that **starts** on a reserved
  address (5006, 5008, 13043, 13047, 13014) is refused with exception 2.
- **Holding reads are slow (0.1-0.5 s) and answer exception 4 at random**
  while HA polls — every failed read succeeded on a repeat, including the
  start/stop register (12999 reads back 0xCF) and 13059 (0xFFFF). 13017×83
  (the whole Table 4 block) and 33046×104 answer in one frame. Exception 4
  is contention; `RetryingUnit` handles it (0 retries needed on a full
  sweep when HA happened to be quiet).
- Ranges are now: input `(4951, 5034) (5213, 5241) (5600, 5638) (5722, 5745)
  (12999, 13078) (13249, 13293)`; holding `(13017, 13099) (31212, 31212)
  (33046, 33149)`; `MAX_SPAN = 100`. A full live sweep is 22 reads (setup 6,
  realtime 9, slow 7; 25 with `--raw`), ~2 s — it was 36 under the M1 map.
  The M1 map is kept as `NARROW_*_RANGES`: a component whose merged block
  is refused (exception 2) is re-planned against it once, so a stricter
  dongle degrades to more reads, not to a dead component.
- **mkaiser's meter phase voltages/currents (5741-5746) are not served**:
  a read starting at 5740 is refused, and inside a 5722×24 block the six
  words read 0 (the mkaiser entities are `unavailable` on the same system).
  Dropped from the library. The documented backup voltages and frequency
  (regs 5731-5734) *are* served and were added to `Backup`.
- Values matched the mkaiser entities at the same moment for every field
  the library models (static ones identical; live ones within their poll
  drift). Notes: BDC
  rated power (reg 5628) reports **30 000 W on a 15 kW SH15T**, so the
  `battery_max_power_w` default must not be the BDC rating alone — the
  integration option should default to `min(bdc_rated_power, nominal_power)`
  and Dan sets 12 000; start power 33148/33149 are served but read 0xFFFF
  (mkaiser shows 655 350 W, the library shows None); protocol version
  register says V1.1.7 on P063; the settings block showed a leftover
  `charge_command = discharge`, `forced_power = 10000` under
  `ems_mode = self_consumption` — inert, exactly the case the guarded write
  layer must tolerate; 13049/13050 are genuinely served (13050 read 0xBB).
  The reserved registers inside 13017-13099 read 0xFFFF **except 13052 and
  13079, which read 0** — so "0 for unserved" is not uniform on this dongle
  and a 0 is never proof either way; M3 must not treat `ems_mode == 0` as
  evidence of anything without the `running_state` cross-check.
- `scripts/survey.py` (read-only) reproduces the survey; a user on other
  firmware sends its table when a block the library reads is refused.
- CLI lesson: `add_connection_args(connections=(("tcp", None), ("serial",
  "rtu")))` makes the helper default tcp to **RTU-over-TCP** (one named
  framer becomes the default for all transports); a WiNet-S never answers
  that. Name the socket framer explicitly.

### 3.4 mkaiser's "things that will bite you", applied to this design

Read in full: `CLAUDE.md` on their `proper-ha-integration` branch. Each item
below is one of their verified findings and what it changes here.

- **Version pin drift kills the config flow.** HA compares the manifest's
  `requirements` pin against the *installed* library on every start and
  pip-installs on mismatch; an editable install's metadata does not follow a
  version bump. → `scripts/sync_version.py --set X.Y.Z` moves the library
  version and the manifest pin together **and re-installs the editable
  package**; `--check` runs in CI and in pytest. Never edit either by hand.
- **Importing `homeassistant.components.modbus` needs
  `modbus-connection[tmodbus]`** (core uses the tmodbus backend, not pymodbus).
  → in the dev group, pinned to core's manifest; bump together.
- **Domain vs library import name.** Their domain equals their library's
  import name (`sungrow_modbus`), so putting `custom_components/` on
  `PYTHONPATH` shadowed the library and surfaced as a circular import. → ours
  differ (`sungrow` vs `sungrow_inverter`) by construction; still never put
  `custom_components/` on `PYTHONPATH` — the HA dev instance uses a symlink
  and tests use `pytest-homeassistant-custom-component`.
- **Ask the inverter, do not infer.** → gate on device type (input 5000) and
  read output type (5002) rather than assuming three phases; capability by
  transport differs (WiNet-S forwards ~1020 of ~1510 addresses).
- **Padded frames at input 2612/2628** (the "Sungrow Version 3/4" strings)
  make modbus-connection reject the whole block; a raw-socket client reads
  them fine and misleads. → we do not read the 2582–2639 block at all
  (firmware comes from 13250+); M2's survey uses the library (strict) client,
  never a lenient one.
- **The WiNet-S answers an empty string where the LAN port refuses** (13249 /
  13264 / 13279 arrive as all-0x00 through the dongle) and **0 where the
  inverter says 0xFFFF**. → `present()` maps `""` to `None`; MPPT3 comes from
  the model table, not from probing; "the block answered" is not evidence.
- **A timeout is not evidence about a register**; only exception 0x02 is.
  Narrowing a failing block over a slow link can cost minutes per block. →
  M2 records refusals and timeouts separately and never derives a range from
  a timeout.
- **Not every register answers; a poll losing one block is normal.** →
  `UpdateReport` per component; energy totals stay available.
- **Which unit ids answer depends on the transport**, and an open port 502 is
  not necessarily an inverter. → the config flow asks host/port/unit
  explicitly (unit 1 default) and validates by reading the identity block;
  the error names the exception type.
- **A WiNet-S serves Modbus over TLS on 516** with what looks like a shared
  firmware certificate (authenticates nothing). → not needed now;
  `ModbusTlsParams(verify=False)` is a cheap later option.
- **HACS reads the default branch unless there is a stable release.** → our
  default branch `main` carries `custom_components/`, so pre-releases work;
  a stable tag is still what makes "cut a release" mean "available".
- **Python ≥ 3.14.2 for HA 2026.9** (devcontainer images stop at 3.13).
- **Use a simulator, not real hardware, for the dev loop.** → optional but
  cheap: serve the captured fixture on `localhost:5020` (modbus-connection's
  `tests/modbus_server.py` shape) so the HA dev instance and the CLI run
  without touching the inverter.

### 3.5 What Numbat needs from the integration

Two orthogonal axes, applied in order restore → export cap → battery mode:

| Desired state | Registers |
|---|---|
| self_consumption (idle, failsafe) | EMS 13050 = 0; limits at rated |
| forced_charge(power_w) / forced_discharge(power_w) | 13052 = power **then** 13051 = 0xAA/0xBB **then** 13050 = 2 |
| no_charge | EMS 0 + max charge power 33047 = 0 (discharge limit rated) |
| hold | EMS 0 + 33047 = 0 **and** 33048 = 0 |
| export limit 0 / rated (curtail / uncurtail) | 13087 = 0xAA + 13074 = 0 / rated (bounds 5622/5623) |
| pv_limitation on/off | 13018 = 0xAA / 0x55 |
| start / stop | holding 13000 = 0xCF / 0xCE |

Rules: skip a write when the last-polled value already matches; verify by
read-back (`async_refresh`); restore limits before switching branches except
while the target state needs them; report what was written; typed errors.

Entity-ID continuity: Numbat's config and 175 days of long-term statistics use
`sensor.load_power`, `sensor.battery_level`, `sensor.battery_power` (mkaiser
convention: positive = discharging). After install Dan renames the new entities
to those IDs (HA migrates statistics on rename) or repoints Numbat.

## 4. Architecture

Three layers, exactly as the HA docs recommend:

1. `modbus-connection` (HA-owned library) — connection, unit handles, block
   pooling, field codecs, mock backend.
2. `sungrow-inverter` (this repo, PyPI) — `SungrowInverter` device object over
   typed `Component`s, the SH-T model gate, the WiNet-S retry wrapper, and
   `BatteryControl` (the guarded desired-state layer). No HA imports.
3. `custom_components/sungrow` (this repo, HACS) — config flow, two
   coordinators, entities, actions, diagnostics. Thin.

Verified against HA 2026.9.1 source: `homeassistant.components.modbus`
exports `async_get_unit(hass, entry, params, unit_id)` and
`async_get_temporary_unit(hass, params, unit_id)` — no compat shim needed.
HA 2026.9.1 requires **Python ≥ 3.14.2** (Dan has 3.14.6 via uv); the library
supports ≥ 3.12 (modbus-connection's floor). modbus-connection 4.10.0 ships
the typed exception subclasses (`ServerDeviceFailureError` = code 4,
`IllegalDataAddressError` = code 2, …).

## 5. Repo skeleton

```
hass-sungrow-modbus/
├── pyproject.toml            # uv workspace root (requires-python >=3.14.2); dev group only
├── uv.lock
├── README.md  LICENSE (MIT)  NOTICE.md (mkaiser MIT attribution + Sungrow spec)  CHANGELOG.md
├── hacs.json                 # {"name":"Sungrow SH-T Hybrid Inverter","homeassistant":"2026.9.0","render_readme":true}
├── .github/workflows/
│   ├── ci.yml                # lib: ruff format/check, mypy --strict, pytest (3.13 + 3.14); integ: ruff, mypy, pytest (3.14); hassfest; HACS action
│   ├── publish-library.yml   # on tag lib-v*: sed version into packages/sungrow-inverter/pyproject.toml, uv build, PyPI trusted publishing
│   └── release-integration.yml  # on tag v*: assert manifest.version == tag
├── scripts/check.sh          # ruff format --check, ruff check, mypy, pytest (both packages)
├── packages/sungrow-inverter/
│   ├── pyproject.toml        # name="sungrow-inverter", hatchling, version="0.0.0" (patched from tag),
│   │                         # requires-python>=3.12, deps ["modbus-connection>=4.10,<5"], extras cli=["modbus-connection[tmodbus]>=4.10,<5"]
│   ├── README.md
│   ├── scripts/query.py      # read-only CLI dump (tcp/serial) through RetryingUnit; --raw dumps async_read_raw() JSON
│   ├── src/sungrow_inverter/
│   │   ├── __init__.py  py.typed
│   │   ├── const.py          # INPUT_RANGES, HOLDING_RANGES, register constants, 0xAA/0x55/0xCF/0xCE
│   │   ├── enums.py          # InverterState, PowerFlow, EmsMode, ChargeCommand, StartStop, BatteryMode
│   │   ├── models.py         # SHT_MODELS, ShtModel, model_for(), UnsupportedModelError (names known non-SHT codes)
│   │   ├── fields.py         # aa55(), helpers with Sungrow word order / sentinels
│   │   ├── components/       # identity.py firmware.py realtime.py battery.py energy.py alarms.py settings.py control.py
│   │   ├── report.py         # UpdateReport, WriteRecord, WriteReport
│   │   ├── retry.py          # RetryPolicy, RetryingUnit
│   │   ├── control.py        # BatteryControl: apply(), set_export_limit(), set_pv_limitation(), start(), stop(), effective_mode()
│   │   ├── inverter.py       # SungrowInverter device object + async_probe()
│   │   └── exceptions.py
│   └── tests/
│       ├── conftest.py       # SH15T-shaped INPUT/HOLDING seeds; `unit`/`inverter` fixtures; FlakyUnit
│       ├── fixtures/sh15t_p063.json   # real async_read_raw() dump captured in M2, replayed with load_raw()
│       └── test_decode.py test_planning.py test_models.py test_inverter.py test_control.py test_retry.py
├── custom_components/sungrow/
│   ├── __init__.py manifest.json const.py config_flow.py coordinator.py entity.py
│   ├── sensor.py binary_sensor.py number.py select.py switch.py button.py
│   ├── services.py services.yaml diagnostics.py strings.json translations/en.json icons.json quality_scale.yaml
└── tests/                    # integration tests (pytest-homeassistant-custom-component)
    ├── conftest.py           # patches custom_components.sungrow.async_get_unit / _temporary_unit to a MockModbusUnit loaded from the fixture
    └── test_config_flow.py test_init.py test_sensor.py test_number_select_switch.py test_services.py test_diagnostics.py
```

Root `pyproject.toml` essentials:

```toml
[project]
name = "hass-sungrow-modbus"
version = "0.0.0"
requires-python = ">=3.14.2"
[tool.uv.workspace]
members = ["packages/sungrow-inverter"]
[tool.uv.sources]
sungrow-inverter = { workspace = true }
[dependency-groups]
dev = [
  "sungrow-inverter",
  "pytest-homeassistant-custom-component==0.13.364",   # pins homeassistant==2026.9.1
  "modbus-connection[tmodbus]>=4.10,<5",                 # HA's modbus/connection.py imports modbus_connection.tmodbus
  "pytest>=8", "pytest-asyncio>=0.24", "ruff>=0.15", "mypy>=1.19",
]
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests", "packages/sungrow-inverter/tests"]
```

**Library delivery during development.** Tests never need PyPI (uv workspace).
Dan's HAOS cannot `pip install -e`, so publish PyPI **pre-releases** from day
one (`lib-v0.1.0a1`, `a2`, …) via trusted publishing; the manifest pins
`"requirements": ["sungrow-inverter==0.1.0a1"]` and each alpha bump makes HA
reinstall. Install the integration by copying `custom_components/sungrow` to
`/config/custom_components/` (or add the repo as a HACS custom repository).

## 6. Library design (`sungrow_inverter`)

### 6.1 Conventions
- Addresses are protocol addresses (spec register − 1); every field docstring
  carries `(reg N)`.
- 32-bit values: `word_order="little"` (= mkaiser `swap: word`).
- Sentinels: U16 `nan=0xFFFF`, S16 `nan=0x7FFF`, U32 `nan=0xFFFFFFFF`,
  S32 `nan=0x7FFFFFFF`.
- `aa55(addr)` → `NumberField(addr, signed=False, convert={0xAA: True, 0x55: False},
  nan=0xFFFF, writable=lambda v: 0xAA if v else 0x55)`. M1 test must prove
  the write path round-trips through `NumberField.encode`; otherwise fall back
  to `integer()` + a property.
- `battery_power` keeps mkaiser's sign (raw 5214: positive = discharging) so
  the renamed entity is a drop-in for Numbat's `discharge_positive` convention.

### 6.2 Enums
- `InverterState(StrEnum)` from Appendix 2 via `NumberField(convert=RUNNING_STATES)`
  (dual codes map to the same member, e.g. 0x0000 and 0x0040 → running;
  0x8100 derating_running, 0x8200 dispatch_running, 0x1300 key_stop …);
  `running_state_raw` keeps the word.
- `PowerFlow(IntFlag)`: PV_GENERATING=1, BATTERY_CHARGING=2,
  BATTERY_DISCHARGING=4, LOAD_POSITIVE=8, EXPORTING=16, IMPORTING=32,
  LOAD_NEGATIVE=128.
- `EmsMode(IntEnum)`: SELF_CONSUMPTION=0, COMPULSORY=2, EXTERNAL_EMS=3, VPP=4.
- `ChargeCommand(IntEnum)`: CHARGE=0xAA, DISCHARGE=0xBB, STOP=0xCC.
- `StartStop(IntEnum)`: START=0xCF, STOP=0xCE.
- `BatteryMode(StrEnum)`: desired — self_consumption, forced_charge,
  forced_discharge, no_charge, hold; effective-only — forced_stop,
  no_discharge, external_ems, vpp, unknown.

### 6.3 Model gate
```python
SHT_MODELS = {0x0E20: ShtModel("SH5T", mppt=2), 0x0E21: ("SH6T", 2), 0x0E22: ("SH8T", 2),
              0x0E23: ("SH10T", 2), 0x0E24: ("SH12T", 2), 0x0E25: ("SH15T", 3),
              0x0E26: ("SH20T", 3), 0x0E28: ("SH25T", 3)}   # 0x0E27 unassigned
def model_for(code: int) -> ShtModel   # raises UnsupportedModelError(code, known_name | None)
```
`OTHER_MODELS` (mkaiser's table) is used only to name the rejected model in the
config-flow error ("SH10RT (0x0E03) is not an SH-T inverter").

### 6.4 Components and readable ranges
M1 started from a conservative cut split at every documented reserved hole;
M2 widened the ranges to the blocks the WiNet-S proved it serves (§3.3a). No
`ComponentGroup` on the polling path (a refused block would fail the group).

```python
# Widened in M2 to what the WiNet-S survey (§3.3a) proved; the M1 first cut
# split at every documented hole.
INPUT_RANGES = ((4951, 5034), (5213, 5241), (5600, 5638), (5722, 5745),
                (12999, 13078), (13249, 13293))
HOLDING_RANGES = ((13017, 13099), (31212, 31212), (33046, 33149))
class SungrowInput(Component):   register_space = "input";   register_ranges = INPUT_RANGES;   max_span = 100
class SungrowHolding(Component): register_space = "holding"; register_ranges = HOLDING_RANGES; max_span = 100
```

| Component | Space | Fields (addr → name) | Poll |
|---|---|---|---|
| `Identity` | input | 4951 protocol_version u32; 4953 arm_version str15; 4968 dsp_version str15; 4989 serial str10; 4999 device_type_code; 5000 nominal_power ×100 W; 5001 output_type — two adjacent blocks only, so the model gate cannot be stopped by a refused ratings register | once |
| `Ratings` | input | 5621/5622 export_limit_min/max ×10 W; 5627 bdc_rated_power ×100 W; 5634/5635 bms_max_charge/discharge_current; 5638 battery_capacity ×0.01 kWh | once, optional |
| `FirmwareInfo` | input | 13249 inverter_firmware str15; 13264 comm_module_firmware str15; 13279 battery_firmware str15 | once, optional |
| `AcDc` | input | 5010–5015 mppt1–3 voltage/current ×0.1 (mppt3 `restrict_fields` off on 2-MPPT models); 5016 total_dc_power u32; 5018–5020 phase voltages ×0.1; 5032 reactive_power s32; 5034 power_factor ×0.001; 5241 grid_frequency ×0.01 | 10 s |
| `Flows` | input | 12999 running_state_raw + running_state; 13000 power_flow flags; 13007 load_power s32; 13009 export_power s32 (positive = export; both nan 0x7FFFFFFF) | 10 s |
| `GridPhases` | input | 13030–13032 phase currents ×0.1 signed; 13033 total_active_power s32 | 10 s |
| `Meter` | input | 5600 meter_active_power s32; 5602/5604/5606 per-phase (all nan 0x7FFFFFFF: no meter / single-phase meter) | 10 s |
| `MeterPhases` | input | 5740–5742 meter voltages ×0.1 (nan 0x7FFF); 5743–5745 meter currents ×0.01 — undocumented (mkaiser), own component so a refusal cannot take `Meter` down | 10 s, optional |
| `Backup` | input | 5722–5724 backup phase power s16; 5725 total_backup_power s32 | 10 s |
| `Battery` | input | 5630 battery_current ×0.1 signed; 13019 voltage ×0.1; 13022 level ×0.1 %; 13023 soh ×0.1 %; 13024 temperature ×0.1 signed | 10 s |
| `BatteryPower` | input | 5213 battery_power s32 W (own component — refused on some paths) | 10 s |
| `Energy` | input | 5002/5003 daily/total output; 5007 inverter_temperature; 13001/13002 pv generation; 13004/13005 export from PV; 13011/13012 battery charge from PV; 13016/13017 direct consumption; 13025/13026 battery discharge; 13035/13036 import; 13039/13040 battery charge; 13044/13045 export (kWh ×0.1) | 60 s |
| `Alarms` | input | 13049–13077 alarm words (u32 raw) | 60 s, optional |
| `Settings` | holding | 13017 pv_power_limitation aa55; 13049 ems_mode enum(writable); 13050 charge_command enum(writable); 13051 forced_power W (writable); 13057/13058 max/min_soc ×0.1 (writable); 13073 export_limit W (writable); 13074 backup_mode aa55; 13086 export_limit_enabled aa55; 13087 feed_in_ratio ×0.1 % nan; 13088 active_power_limit_enabled aa55; 13089 active_power_limit_ratio ×0.1 % nan; 13099 backup_reserve_soc % (writable) | 60 s + after writes |
| `BatteryLimits` | holding | 33046 max_charge_power gauge(scale 10, W, writable=multiple_of_10); 33047 max_discharge_power | 60 s + after writes |
| `StartPower` | holding | 33148/33149 charging/discharging_start_power ×10 nan 0xFFFF | 60 s, optional |
| `AplShadow` | holding | 31212 apl_shutdown_at_zero aa55 (undocumented, read-only diagnostic) | 60 s, optional |
| `Control` | holding | 12999 start_stop integer(writable) — write-only, never polled | — |

Representative declaration:

```python
class Settings(SungrowHolding):
    """EMS, battery and limit settings (Table 4). Polled slowly: RW registers must not be hammered through a WiNet-S."""
    pv_power_limitation = aa55(13017)
    """PV power limitation (reg 13018): True = 0xAA limit PV, False = 0x55 allow. SH-T only."""
    ems_mode = enum(13049, EmsMode, writable=True)
    """EMS mode selection (reg 13050)."""
    charge_command = enum(13050, ChargeCommand, writable=True)
    """Charge/discharge command (reg 13051): 0xAA charge, 0xBB discharge, 0xCC stop."""
    forced_power = integer(13051, signed=False, unit="W", writable=_non_negative)
    """Charge/discharge power (reg 13052), W."""
    max_soc = gauge(13057, 0.1, signed=False, unit="%", writable=_soc_50_100)
    min_soc = gauge(13058, 0.1, signed=False, unit="%", writable=_soc_0_50)
    export_limit = integer(13073, signed=False, unit="W", writable=_non_negative)
    """Feed-in limitation value (reg 13074), W — 1 W/count on SH-T."""
    backup_mode = aa55(13074)
    export_limit_enabled = aa55(13086)
    feed_in_ratio = gauge(13087, 0.1, signed=False, unit="%", nan=0xFFFF, writable=_ratio)
    active_power_limit_enabled = aa55(13088)
    active_power_limit_ratio = gauge(13089, 0.1, signed=False, unit="%", nan=0xFFFF, writable=_ratio)
    backup_reserve_soc = integer(13099, signed=False, unit="%", writable=_pct)
```

### 6.5 Device object (`inverter.py`)
```python
class SungrowInverter:
    def __init__(self, unit: ModbusUnit, *, battery_max_power_w: int | None = None, fence_power_w: int = 10): ...
    model: ShtModel | None                                    # detected by the first update; the wire is authoritative (no kwarg)
    @classmethod
    async def async_probe(cls, unit) -> ProbeResult          # Identity (+ optional Ratings); raises UnsupportedModelError
    async def async_update_realtime(self, *, collect_raw=False) -> UpdateReport   # ac_dc, flows, grid_phases, meter, meter_phases, backup, battery, battery_power
    async def async_update_settings(self, *, collect_raw=False) -> UpdateReport   # settings, battery_limits, energy, start_power, apl_shadow, alarms
    async def async_read_raw(self) -> Raw                     # one poll with collect_raw; a failing component is left out, not fatal
    def last_refresh(self, name) -> float | None              # time.monotonic() of the last successful read — the M3 freshness guard reads this
    battery: Battery                                          # the measurement component
    battery_control: BatteryControl;  effective_battery_mode: BatteryMode | None   # M3 (named battery_control: `battery` is taken)
    battery_max_power_w: int | None  # option, else min(nominal_power, bdc_rated_power); None = unknown → SettingsUnavailableError in M3, never 0
    async def async_refresh(self, *names, collect_raw=False) -> UpdateReport   # e.g. ("settings", "battery_limits") before/after a write; updates last_refresh
```
`_async_setup()` on first update: read `identity`, apply `restrict_fields`
for the MPPT count, probe optionals (`IllegalDataAddress`/`IllegalFunction` →
`None`), build the poll lists. `_async_poll()`/`_notify()` follow
modbus-connection's `patterns_library.md` verbatim (`ModbusConnectionError`
re-raised; first timeout re-raised; later failures recorded per component).

### 6.6 WiNet-S retry policy (`retry.py`)
```python
@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3                    # 1 try + 2 retries, 0.25 s then 0.5 s
    base_delay: float = 0.25
    retry_codes: frozenset[int] = frozenset({4, 6, 10, 11})   # device failure, busy, gateway path/target
    retry_timeouts: bool = True

class RetryingUnit:   # wraps the ModbusUnit HA hands out; the library never sees the difference
    async def _call(self, op, *args):
        for attempt in range(1, self._policy.attempts + 1):
            try:
                return await op(*args)
            except ModbusExceptionError as err:
                if err.exception_code not in self._policy.retry_codes or attempt == self._policy.attempts: raise
            except ModbusTimeoutError:
                if not self._policy.retry_timeouts or attempt == self._policy.attempts: raise
            await asyncio.sleep(self._policy.base_delay * 2 ** (attempt - 1))
```
Never retries codes 1/2/3 (real refusals the planner must see). Retries happen
per block read inside `Component.async_update`, so a flaky block does not fail
the component; a component still failing lands in `report.failed`, its entities
go unavailable for one poll. The realtime coordinator calls `unit.disconnect()`
only after 3 consecutive `ModbusTimeoutError`s (docs pattern), never on code 4.

### 6.7 Guarded write layer (`control.py`)
Decisions:
- `no_charge`/`hold` use the max charge/discharge power fences (as Numbat and
  evcc do), **not** EMS compulsory + STOP (STOP churns the EMS register on every
  re-assert, and a lost revert leaves the battery entirely inert).
- Fence = **10 W (raw 1)**, matching the register's documented minimum; "fenced"
  = `<= fence_power_w`. M3 checks whether raw 0 is accepted; the fence stays 10.
- Restore target = `battery_max_power_w` (option; default BDC rated power reg
  5628; Dan sets 12000).
- `self_consumption` writes EMS = 0 and restores both limits; it does not touch
  13050/13051 (a leftover command is inert in self-consumption).
- **Order:** restore limits the target does not fence → fence limits the target
  needs → power → command → EMS mode. Power before mode; leaving forced mode is
  the last write so a partial failure leaves the previous mode intact rather
  than "forced with a stale setpoint".
- **Guard:** skip a write when the last-polled value already equals the target,
  compared at register resolution (`encode(target) == encode(current)`) so
  12000.0 vs 12000 and 10-W rounding never cause spurious writes.
- **Freshness:** if the settings snapshot is older than 30 s, re-read
  `settings` + `battery_limits` before planning.
- **Verify:** after the writes, sleep 0.5 s, re-read `settings` +
  `battery_limits`, compare every planned target; mismatch →
  `VerificationError(field, expected, actual, report)`. The re-read doubles as
  the refresh the integration displays.
- **Atomicity:** one `asyncio.Lock` per inverter around `apply`/`set_*`.
- Errors: `SungrowError` → `UnsupportedModelError`, `ControlError` →
  `PowerOutOfRangeError`, `WriteRejectedError(field, cause)`,
  `VerificationError`, `SettingsUnavailableError`.

```python
@dataclass(frozen=True)
class DesiredState:
    mode: BatteryMode            # self_consumption | forced_charge | forced_discharge | no_charge | hold
    power_w: int | None = None   # required for forced_*; clamped to [0, battery_max_power_w]

class BatteryControl:
    def _plan(self, d: DesiredState) -> list[_Step]:
        s, lim, full, fence = "settings", "battery_limits", self._inv.battery_max_power_w, self._inv.fence_power_w
        match d.mode:
            case BatteryMode.SELF_CONSUMPTION:
                return [_Step(lim, "max_charge_power", full), _Step(lim, "max_discharge_power", full),
                        _Step(s, "ems_mode", EmsMode.SELF_CONSUMPTION)]
            case BatteryMode.FORCED_CHARGE | BatteryMode.FORCED_DISCHARGE:
                cmd = ChargeCommand.CHARGE if d.mode is BatteryMode.FORCED_CHARGE else ChargeCommand.DISCHARGE
                return [_Step(lim, "max_charge_power", full), _Step(lim, "max_discharge_power", full),
                        _Step(s, "forced_power", self._clamp(d.power_w)),        # power before mode
                        _Step(s, "charge_command", cmd), _Step(s, "ems_mode", EmsMode.COMPULSORY)]
            case BatteryMode.NO_CHARGE:
                return [_Step(lim, "max_discharge_power", full), _Step(lim, "max_charge_power", fence),
                        _Step(s, "ems_mode", EmsMode.SELF_CONSUMPTION)]
            case BatteryMode.HOLD:
                return [_Step(lim, "max_charge_power", fence), _Step(lim, "max_discharge_power", fence),
                        _Step(s, "ems_mode", EmsMode.SELF_CONSUMPTION)]

    async def apply(self, desired: DesiredState, *, verify: bool = True) -> WriteReport:
        async with self._lock:
            await self._ensure_fresh_snapshot()
            report = WriteReport(mode=desired.mode)
            for step in self._plan(desired):
                component = getattr(self._inv, step.component)
                current = getattr(component, step.field)
                if _same_on_wire(component, step.field, current, step.target):
                    report.skipped.append(f"{step.component}.{step.field}"); continue
                try:
                    await component.write(step.field, step.target)
                except ModbusError as err:
                    raise WriteRejectedError(step.field, err, report) from err   # earlier writes stand
                report.writes.append(WriteRecord(step.component, step.field, current, step.target))
            if verify and report.writes:
                await asyncio.sleep(self._settle)
                await self._refresh_settings()
                for step in self._plan(desired):
                    actual = getattr(getattr(self._inv, step.component), step.field)
                    if not _same_on_wire(..., actual, step.target):
                        raise VerificationError(step.field, step.target, actual, report)
                report.verified = True
            return report

    async def set_export_limit(self, limit_w: int | None) -> WriteReport   # None → 13087 = 0x55; else 13087 = 0xAA + 13074 = clamp(limit, 5622..5623)
    async def set_pv_limitation(self, limit: bool) -> WriteReport           # 13018 = 0xAA / 0x55
    async def start(self) / stop(self)                                       # holding 13000 = 0xCF / 0xCE, no verify (restart ~80–100 s)

    def effective_mode(self) -> BatteryMode | None:
        # COMPULSORY → forced_charge / forced_discharge / forced_stop by charge_command
        # SELF_CONSUMPTION → hold (both fenced) / no_charge / no_discharge / self_consumption
        # EXTERNAL_EMS / VPP → external_ems / vpp;  ems_mode None → None
```
`WriteReport.as_dict()` → `{"mode", "writes": [{component, field, previous,
value}], "skipped": [...], "verified"}` — returned as the action response.
`set_export_limit` warns when `feed_in_ratio` is neither `None` nor 100.0 (the
spec says the ratio register 13088 takes precedence over the W value).

### 6.8 CLI (`scripts/query.py`)
`uv run --package sungrow-inverter python packages/sungrow-inverter/scripts/query.py $SUNGROW_HOST --unit 1 [--raw .testdata/raw.json]`
runs one `async_update(collect_raw=...)` sweep through `RetryingUnit`, prints the
model-gate result, every component (serial masked unless `--show-serial`), the
read and retry counts and failed components, and optionally dumps the raw JSON
(serial replaced by `A123456789` unless `--keep-serial`) for the test fixture. Read-only;
M3 adds `--apply MODE` behind an explicit confirmation flag.

## 7. Integration design (`custom_components/sungrow`)

### 7.1 manifest.json
```json
{ "domain": "sungrow", "name": "Sungrow SH-T Hybrid Inverter", "codeowners": ["@danbitzer"],
  "config_flow": true, "dependencies": ["modbus"],
  "documentation": "https://github.com/danbitzer/hass-sungrow-modbus",
  "issue_tracker": "https://github.com/danbitzer/hass-sungrow-modbus/issues",
  "integration_type": "device", "iot_class": "local_polling",
  "loggers": ["sungrow_inverter", "modbus_connection"],
  "requirements": ["sungrow-inverter==0.1.0a1"], "version": "0.1.0" }
```

### 7.2 Config flow

The config flow (or its options) carries **Battery max power (W)** as an explicit field, as mkaiser's package does with `sungrow_modbus_battery_max_power`: default `min(nominal_power, bdc_rated_power)`, range 10 W to that value, step 10. It is the restore target of `self_consumption`; Dan's is 10 000 W today (he set it; the inverter can do 15 kW).
- `step_user`: `connection_type` (tcp | serial), `host`, `port` (502),
  `unit_id` (1); serial → `step_serial` (device, baudrate 9600, parity N,
  stopbits 1, bytesize 8) → `ModbusSerialParams`. `create_modbus_params(data)`
  mirrors trovis-hass.
- Probe: `async with async_get_temporary_unit(hass, params, unit_id) as unit:
  probe = await SungrowInverter.async_probe(RetryingUnit(unit))`.
  `ModbusError`/`HomeAssistantError` → `cannot_connect`;
  `UnsupportedModelError` → `unsupported_model` with placeholders
  `{model, code}`.
- `async_set_unique_id(probe.serial)`; title `f"Sungrow {model.name}"`; entry
  data = connection fields + `device_type_code` + `serial` + `bdc_rated_power_w`.
- `step_reconfigure` re-probes and requires the same serial.
- Options: `realtime_interval` (5–60 s, default 10), `settings_interval`
  (30–600 s, default 60), `battery_max_power_w` (default BDC rated),
  `default_export_limit_w` (default reg 5623), `message_spacing_ms` (default
  50), `include_register_dump` in diagnostics.

### 7.3 Setup and coordinators
```python
async def async_setup_entry(hass, entry):
    params = create_modbus_params(entry.data)
    try:
        unit = async_get_unit(hass, entry, params, entry.data[CONF_UNIT_ID])
    except HomeAssistantError as err:            # endpoint in use with different link settings
        raise ConfigEntryNotReady(str(err)) from err
    unit.set_message_spacing(entry.options.get(CONF_MESSAGE_SPACING_MS, 50) / 1000)
    device = SungrowInverter(RetryingUnit(unit), model=model_for(entry.data[CONF_DEVICE_TYPE_CODE]),
                             battery_max_power_w=entry.options.get(CONF_BATTERY_MAX_POWER_W))
    realtime = SungrowCoordinator(hass, entry, device, device.async_update_realtime, ..., count_timeouts=True)
    settings = SungrowCoordinator(hass, entry, device, device.async_update_settings, ...)
    await realtime.async_config_entry_first_refresh()   # runs _async_setup (identity, restrict_fields, optional probes)
    await settings.async_config_entry_first_refresh()
    entry.runtime_data = SungrowRuntime(device=device, realtime=realtime, settings=settings)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
```
`SungrowCoordinator(DataUpdateCoordinator[UpdateReport])` is the docs'
coordinator: `ModbusError` → `UpdateFailed`; empty `report.updated` →
`UpdateFailed`; log newly failed components once; timeout counter +
`unit.disconnect()` after 3 only when `count_timeouts=True`; `device_info` from
`identity` (identifiers `{(DOMAIN, serial)}`, manufacturer "Sungrow", model
name, sw_version = firmware string). Extra
`async_apply_settings_snapshot()` = `async_set_updated_data(...)` so entities
refresh right after an action without a second poll. Actions are registered
once in `async_setup` (integration-wide); handlers resolve the entry from
`device_id`.

### 7.4 Entity platforms (keys mirror mkaiser object ids; `has_entity_name=True`)
`SungrowEntity(CoordinatorEntity)`: `unique_id = f"{serial}_{key}"`,
`available = super().available and report_name in coordinator.data.updated`.
Energy totals use `RestoreSensor` (always available, 1 % dip filter) per the
docs.

- **sensor** (realtime unless S): power — `total_dc_power`, `mppt1/2/3_power`
  (derived V×I), `total_active_power`, `reactive_power`, `battery_power`
  (5214 sign kept), `battery_charging_power`/`battery_discharging_power`
  (derived ≥ 0), `load_power`, `export_power_raw`, `import_power`,
  `export_power`, `meter_active_power` (+ per phase), backup powers; electrical
  — MPPT/phase/meter voltages & currents, `battery_voltage/current`,
  `grid_frequency`, `power_factor`; battery — `battery_level`,
  `battery_state_of_health`, `battery_temperature`, `inverter_temperature`
  (S), `battery_capacity_high_precision` (diag); state — `running_state_raw`
  (diag), `sungrow_inverter_state` (ENUM), `power_flow_status` (diag),
  **`battery_mode`** (S, ENUM; attrs forced_power_w, max_charge_power_w,
  max_discharge_power_w, export_limit_w, export_limit_enabled, pv_limited,
  battery_max_power_w); energy (S) — all daily (TOTAL_INCREASING) / total
  (TOTAL) kWh counters + derived consumed energy; diagnostic settings mirrors
  (S) — export limit min/max, feed-in ratio, APL ratio, APL shutdown shadow,
  start powers (if present), device type/code, firmware strings, protocol
  version, BMS max currents, alarm words (disabled by default).
- **binary_sensor** (from `PowerFlow`): `pv_generating`, `battery_charging`,
  `battery_discharging`, `positive_load_power`, `exporting_power`,
  `importing_power`, `negative_load_power`; derived **`grid_connected`**
  (CONNECTIVITY) for the Numbat blueprint's grid sensor.
- **number** (CONFIG, S, `PARALLEL_UPDATES = 1`, `component.write` then
  `async_refresh`): `battery_forced_charge_discharge_power` (0..BDC rated,
  step 100), `battery_max_charge_power`/`_discharge_power` (10..battery_max,
  step 10), `battery_min_soc` (0..50), `battery_max_soc` (50..100),
  `battery_reserved_soc_for_backup` (0..100), `export_power_limit` (5622..5623,
  step 100), `active_power_limit_ratio` (only when not None).
- **select**: `ems_mode`, `battery_forced_charge_discharge`.
- **switch**: `export_power_limit` (13086), `backup_mode` (13074),
  `pv_power_limitation` (13017), `active_power_limitation` (13088, if present).
- **button**: `start_inverter`, `stop_inverter` (CONFIG; stop disabled by
  default; both log at WARNING with the user id).

### 7.5 Actions (`services.py`, `services.yaml`)
`device_id` is a **field** with a `device` selector filtered to
`integration: sungrow` (hassfest forbids a device filter on `target`);
`supports_response=OPTIONAL`.

```yaml
set_battery_mode:
  fields:
    device_id: {required: true, selector: {device: {integration: sungrow}}}
    mode: {required: true, selector: {select: {options: [self_consumption, forced_charge, forced_discharge, no_charge, hold], translation_key: battery_mode}}}
    power_w: {required: false, selector: {number: {min: 0, max: 30000, step: 100, unit_of_measurement: W, mode: box}}}
    verify: {required: false, default: true, selector: {boolean: {}}}
set_export_limit:
  fields:
    device_id: {required: true, selector: {device: {integration: sungrow}}}
    limit_w:  {required: false, selector: {number: {min: 0, max: 65535, step: 100, unit_of_measurement: W, mode: box}}}
    enabled:  {required: false, default: true, selector: {boolean: {}}}   # false → 13087 = 0x55
set_pv_limitation:
  fields:
    device_id: {required: true, selector: {device: {integration: sungrow}}}
    limit: {required: true, selector: {boolean: {}}}
start_inverter: {fields: {device_id: {required: true, selector: {device: {integration: sungrow}}}}}
stop_inverter:  {fields: {device_id: {required: true, selector: {device: {integration: sungrow}}}}}
```

```python
async def _async_set_battery_mode(call: ServiceCall) -> ServiceResponse:
    entry = _entry_for_device(call.hass, call.data[ATTR_DEVICE_ID])   # ServiceValidationError: device_not_found / entry_not_loaded / not_a_sungrow_device
    runtime = entry.runtime_data
    mode = BatteryMode(call.data["mode"])
    if mode in (BatteryMode.FORCED_CHARGE, BatteryMode.FORCED_DISCHARGE) and "power_w" not in call.data:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="power_required", ...)
    try:
        report = await runtime.device.battery.apply(DesiredState(mode, call.data.get("power_w")), verify=call.data["verify"])
    except PowerOutOfRangeError as err:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="power_out_of_range", ...) from err
    except VerificationError as err:
        runtime.settings.async_apply_settings_snapshot()      # show what the inverter actually holds
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="verification_failed", ...) from err
    except (WriteRejectedError, ModbusError) as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="write_failed", ...) from err
    runtime.settings.async_apply_settings_snapshot()
    _LOGGER.info("%s: battery mode %s applied (%d writes, %d skipped, verified=%s)", ...)
    return report.as_dict() if call.return_response else None
```
`set_export_limit` / `set_pv_limitation` follow the same shape;
`start_inverter` / `stop_inverter` log at WARNING with `call.context.user_id`,
write, then refresh the **realtime** coordinator (running state lives in the
input space).

### 7.6 Diagnostics, strings, quality scale
- `diagnostics.py`: redacted entry data/options, probe result, both reports,
  `battery_mode`, `battery_max_power_w`, and `registers` from
  `async_read_raw()` with the serial block (input 4989–4998) popped; gated by
  the option; `ModbusError` → `{"error": ...}`.
- `strings.json` = `translations/en.json`: config steps/errors
  (`cannot_connect`, `unsupported_model`, `already_configured`), options,
  entity names per key, enum state translations, action names/fields,
  exception keys. `icons.json` for actions and mode sensors.
- `quality_scale.yaml` for tracking only (omit the manifest key): Bronze done;
  Silver except reauth (exempt); Gold diagnostics/translations/reconfigure.

### 7.7 Tests
Library (`mock_modbus_unit` fixture):
- `test_decode.py`: seed words → values (S32 little order, sentinels → None,
  aa55 both ways, running-state dual codes, PowerFlow bits, energy scale).
- `test_planning.py`: every `read_events` block inside `INPUT_RANGES` /
  `HOLDING_RANGES`, none crossing a boundary, block count ≤ N;
  `restrict_fields` drops MPPT3 on 2-MPPT models.
- `test_models.py`: every SHT code → model; `0x0E03`/`0x0E27` →
  `UnsupportedModelError` (named / unnamed).
- `test_inverter.py`: probe; reports; a refused optional component
  (`fail_read(13249, IllegalDataAddressError())`) → `firmware is None`, rest
  updates; `fail_read(33046, …)` lands only `battery_limits` in
  `report.failed`; `async_read_raw` merges spaces.
- `test_control.py` (`unit.on_write` records `(address, value)` in order):
  `self_consumption` already active → **zero writes**, `skipped == 3`;
  `forced_charge(5000)` from self-consumption → exactly `[13051=5000,
  13050=0xAA, 13049=2]`; `forced_charge` from `hold` → `[33046=1200,
  33047=1200, 13051, 13050, 13049]`; `hold` from forced_discharge →
  `[33046=1, 33047=1, 13049=0]`; `no_charge` → `[33046=1, 13049=0]` (+33047
  restored first if fenced); re-apply `hold` → zero writes; power over max →
  `PowerOutOfRangeError`; forced without power → error; ignored 13049 write →
  `VerificationError("ems_mode")`; `fail_write(13050, IllegalDataValueError())`
  → `WriteRejectedError` listing landed writes; `set_export_limit(0)` →
  `[13086=0xAA, 13073=0]`, `None` → `[13086=0x55]`, clamps to 5622/5623;
  `set_pv_limitation(True)` → `[13017=0xAA]`; `effective_mode` table.
- `test_retry.py`: `FlakyUnit` raising code 4 twice then succeeding → read
  succeeds in 3 attempts; code 2 not retried; exhaustion re-raises; writes
  retried on 4.

Integration (`pytest-homeassistant-custom-component`; conftest patches
`custom_components.sungrow.async_get_unit` / `async_get_temporary_unit` to a
`MockModbusUnit` loaded from `fixtures/sh15t_p063.json`):
- `test_config_flow.py`: happy path (title "Sungrow SH15T", unique_id serial),
  `cannot_connect`, `unsupported_model` (seed 4999 = 0x0E03), duplicate abort,
  reconfigure.
- `test_init.py`: setup/unload; `HomeAssistantError` from `async_get_unit` →
  `ConfigEntryNotReady`.
- `test_sensor.py`: `battery_level`, `battery_power` sign, `battery_mode ==
  "self_consumption"`, an energy sensor keeps its value when its component
  fails.
- `test_services.py`: each action → assert the mock's write sequence
  (skipping + ordering end-to-end), response payload, `ServiceValidationError`
  for a bad device id, `HomeAssistantError` on verification failure;
  `sensor.battery_mode` flips to `hold` immediately after the call.
- `test_diagnostics.py`: serial redacted, registers present.

## 8. Numbat follow-up (separate repo, later)
- New blueprint `blueprints/numbat_actuator_sungrow.yaml` (the generic one
  stays): inputs `sungrow_device` (device selector), `export_limit_w` (DNSP),
  and the existing `action_sensor` / `status_sensor` /
  `max_heartbeat_age_minutes` / `grid_sensor` (default the integration's
  `grid_connected`). Keep the 1 s settle delay, `mode: restart`, heartbeat
  failsafe, grid check and the 5-minute sweep with its 15-minute re-assert
  window. Replace the seven action inputs with one call per branch:
  `sungrow.set_export_limit {limit_w: 0 | export_limit_w}` from
  `curtail_wanted`, then `sungrow.set_battery_mode {mode: charge→forced_charge |
  discharge→forced_discharge | no_charge | hold | default self_consumption,
  power_w}`; failsafe = uncurtail + self_consumption. Restore/ordering/guards
  disappear from the blueprint; re-asserts are Modbus-silent by construction.
- DOCS.md: replace the mkaiser guard examples with the one-call version, keep
  the old block in a collapsible "mkaiser YAML package" section, note
  `battery.power_convention` stays for the renamed `sensor.battery_power`,
  mention `grid_connected`; later a `pv_cut` attribute →
  `sungrow.set_pv_limitation` for negative buy prices.
- CHANGELOG `## Unreleased` entry; README pairing note.

## 9. Milestones (build order; each with its verification)

| # | Deliverable | Verify |
|---|---|---|
| M0 | Repo skeleton, uv workspace (Python 3.14 for the integration), CI (ruff/mypy/pytest/hassfest/HACS), LICENSE + NOTICE, repo `CLAUDE.md` with §2a, `.testdata/` gitignored, `tests/test_no_real_serials.py`, `scripts/sync_version.py` (`--set` re-installs, `--check` in CI + pytest), PyPI trusted-publishing project + `lib-v0.0.1a0` dry run | `uv sync && scripts/check.sh` green on empty tests; hassfest passes on the stub manifest; `python -c "from homeassistant.components.modbus import async_get_unit"` in the workspace venv; the serial test fails on a planted `A2…` string and passes on `A123456789` |
| M1 | Library: enums, models, fields, components, `SungrowInverter`, `UpdateReport`, `RetryingUnit`, CLI | library tests green against the mock (spec-derived seeds); `query.py --help` runs without a backend |
| M2 | Read-only live run: `query.py $SUNGROW_HOST` while the mkaiser YAML is still loaded; capture `--raw` into `tests/fixtures/sh15t_p063.json`; record which merged blocks the WiNet-S serves; widen the ranges. Also record: whether holding 13049/13050 are genuinely served (a WiNet-S answers 0 for an unserved register, and 0 is a valid EMS mode — the M3 write guard must not trust a 0 it cannot distinguish; cross-check `ems_mode` against `running_state` COMPULSORY/EXTERNAL_EMS); whether the undocumented 13014–13015 hole inside the `Energy` block reads; whether `MeterPhases` (5740–5745) and `Ratings` answer | values match the mkaiser entities in HA at the same moment (battery_level, load_power, total_dc_power, battery_power sign, settings); no exception-4 storms with retries on; fixture replays green |
| M3 | `BatteryControl` + control tests; CLI `--apply` (guarded) | with the Numbat actuator **disabled**: `apply(self_consumption)` = zero writes; `no_charge` → HA shows max charge power 10; `self_consumption` restores 12000; `forced_charge 2000` for 2 min then back; `set_export_limit 0/12000`; `set_pv_limitation true/false` (13018 verified live); iSolarCloud/HA numbers agree; note whether raw 0 is accepted on 33047 |
| M4 | Integration: config flow, coordinators, sensor/binary_sensor, diagnostics, strings; publish `lib-v0.1.0a1`; install on Dan's HA | config flow rejects a fake non-SHT code in tests; on the real HA both mkaiser and `sungrow` entities coexist briefly and match; diagnostics download works |
| M5 | number/select/switch/button + actions + `sensor.battery_mode`; integration tests for actions | Developer Tools calls with the actuator disabled; response shows writes/skipped/verified; `sensor.battery_mode` tracks; repeat calls are write-free |
| M6 | Cutover + release 0.1.0, in this order: (1) remove the mkaiser modbus YAML and restart; (2) delete the orphaned legacy registry entries for the ids to be reused (`sensor.battery_level`, `sensor.battery_power`, `sensor.load_power`, the numbers/selects Numbat's dashboard references) so each id is free in the registry *and* the state machine; (3) add the `sungrow` integration; (4) **immediately** rename the new entities onto the legacy ids — before their first long-term-statistics compile, so the rename has nothing to migrate and the legacy statistics rows simply continue; (5) switch Numbat to the new blueprint. Later nicety: a "legacy entity ids" option that claims the ids at creation (mkaiser's adoption pattern) so step 4 disappears | `sensor.numbat_status` stays `ok`; each renamed entity's unit and `state_class` equal the legacy entry's; statistics graphs continuous across the switch; one full charge → discharge → idle → hold cycle traced in the automation |

## 10. Risks and mitigations
- **WiNet-S connection limit** while the mkaiser hub is still loaded (M2/M4):
  keep the overlap short, lengthen mkaiser's scan intervals or stop its hub
  during comparison, and prefer comparing against the captured raw dump.
- **Exception-4 storms**: retries + individual component polling; if a block
  never stabilises, split its range (M2 evidence drives the ranges).
- **Entity-id migration**: manual rename; statistics follow if units (W, %,
  kWh) and the `battery_power` sign are identical. A "mkaiser-compatible ids"
  option is a later nicety.
- **Register semantics by firmware**: 13052 unit (W on SH-T, % on older RT),
  13018 SH-T only, 13089/13090 0xFFFF on older firmware (decode `None`,
  entities created only when present), 33148/33149 absent on many SH-T units
  (optional component), raw 0 acceptance on 33047 (fence is 10 W anyway).
- **Feed-in ratio precedence**: warn when 13088 is set; expose it as a
  diagnostic sensor.
- **Numbat contract**: the failsafe must call `set_battery_mode
  self_consumption` + `set_export_limit <dnsp>`; both are idempotent and
  write-free when nothing changed.
- **Leaking identity into a public repo**: the serial-shape test, the
  gitignored `.testdata/`, the scrubbing `--raw` capture and diagnostics
  redaction (§2a). The fixture `sh15t_p063.json` must be scrubbed *at
  capture*, not afterwards.
- **Version-pin drift** between the library and the manifest: only
  `scripts/sync_version.py` touches either (§3.4).

## 11. Reference material kept from the research
- modbus-connection docs/source and the Trovis examples:
  `/private/tmp/claude-501/-Users-dan-Developer-hem/b01167be-955a-4f38-b285-0b31918dbbfa/scratchpad/refs/`
- mkaiser YAML, theunknown86's SHT YAML, spec V1.1.15 text, probe scripts:
  `/private/tmp/claude-501/-Users-dan-Developer-hem/d74c047e-3ba8-47ab-96e8-9455d49e87be/scratchpad/`
- Numbat consumer contract: `/Users/dan/Developer/hem/blueprints/numbat_actuator.yaml`,
  `/Users/dan/Developer/hem/numbat/DOCS.md` (Sungrow section),
  `/Users/dan/Developer/hem/numbat/src/numbat/ha/publisher.py`.
- Upstream to keep an eye on: mkaiser `proper-ha-integration`
  (`scripts/writes.py`, `scripts/layout.py`, `doc/compatibility.md`,
  `doc/device-fingerprints/*.json`), modbus-connection releases (4.11.x adds
  nothing we need but core will bump its pin).
