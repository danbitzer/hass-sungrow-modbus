# Migrating the Numbat actuator to the `sungrow` integration

A hand-over for whoever works on the Numbat repo (`danbitzer/numbat`). It
describes the Home Assistant integration that replaces the mkaiser Sungrow
Modbus YAML package on Dan's install, the contract Numbat can rely on, the
new capability (PV limitation) Numbat's planner may want to use, and the
blueprint rework that follows. Nothing here requires reading the
integration's source; where a fact was verified on Dan's inverter the date
is given.

## 1. What changed on the Home Assistant side

Dan's SH15T hybrid inverter (15 kW, 44.8 kWh battery, WiNet-S dongle) is now
driven by a purpose-built integration, HA domain `sungrow`, from the repo
`danbitzer/hass-sungrow-modbus`. It is installed alongside the mkaiser YAML
package for now; the cutover (removing mkaiser, renaming entities) is the
integration's milestone M6 and happens after the Numbat blueprint below is
ready, so the old actuator stays as a fallback until then.

What the integration gives Numbat that the YAML package could not:

- **Actions that do the whole job.** One call puts the battery into a mode.
  The integration decides which registers differ, writes them in a safe
  order, reads them back, and reports what it did. The guard, restore,
  ordering and read-back logic that accreted in the current blueprint moves
  out of YAML and into tested code.
- **A read-only battery-mode sensor** derived from the registers, so Numbat
  (or a dashboard) can see what the inverter is actually set to.
- **PV limitation**: an SH-T-only register that stops all PV generation.
  This is new capability for Numbat; see section 4.
- **A grid-connected binary sensor** for the blueprint's outage check.

Entity ids on Dan's install today are prefixed `sungrow_sh15t_` (for example
`sensor.sungrow_sh15t_battery_level`). At M6 the entities Numbat reads are
renamed onto the mkaiser ids Numbat already uses, so **Numbat's `entities`
config does not change**: `sensor.load_power`, `sensor.battery_level`,
`sensor.battery_power` (positive = discharging, the same convention as
mkaiser; `battery.power_convention: charge_negative` stays — the sign is
load-bearing for Numbat and is pinned by the same integration test that
pins the renamed sensors' units). The blueprint does not read those
sensors, it only reads Numbat's own.

## 2. The actions (the contract)

All five actions take `device_id` (the inverter device; a `device` selector
filtered to `integration: sungrow` in a blueprint input) and return an
optional response. All except start/stop take `verify` (default `true`).

| Action | Fields | What it does |
|---|---|---|
| `sungrow.set_battery_mode` | `mode`, `power_w` (forced modes only), `verify` | Puts the battery into one of six modes (below). |
| `sungrow.set_export_limit` | `limit_w` (optional), `enabled` (default `true`), `verify` | Caps grid export (feed-in limitation). Empty `limit_w` lifts the limitation; a DNSP cap is restored by passing its watts. |
| `sungrow.set_pv_limitation` | `limit` (bool), `verify` | `true` stops all PV generation, `false` allows it. |
| `sungrow.start_inverter` | | Boots the inverter. Minutes to first generation. |
| `sungrow.stop_inverter` | | Puts the battery into self-consumption first, then shuts the inverter down. It never restarts itself and a stopped hybrid does not enter backup mode. Numbat must never call this. |

### 2.1 Battery modes

| `mode` | Registers, in words | Numbat action it serves |
|---|---|---|
| `self_consumption` | EMS mode = self-consumption; max charge and max discharge power restored to the battery-max-power option | `idle`, `curtail`, and the failsafe |
| `forced_charge` | limits restored, forced power = `power_w`, command = charge, EMS mode = forced | `charge` |
| `forced_discharge` | as above with command = discharge | `discharge` |
| `no_charge` | EMS self-consumption, max charge power fenced (10 W), max discharge restored | `no_charge` |
| `no_discharge` | mirror of `no_charge` | (unused today) |
| `hold` | EMS self-consumption, both limits fenced | `hold` |

`power_w` is a magnitude in watts, 0 to the **battery max power option**.
The option is the inverter's capability (Dan: 15 000 — set it there; it was
left at 10 000 during M5 testing), and every non-fenced mode restores both
limit registers to it, so self-consumption may serve the house at the full
inverter rate by design. The everyday wear caps and the spike boost live in
Numbat (`battery.max_charge_kw` / `max_discharge_kw`, `spike.discharge_kw`)
and size every forced move. Above the option the call is refused.

### 2.2 Guarantees

These hold for every action and are what lets the blueprint shrink:

- **Guarded.** A register is written only when the last-polled value differs
  from the target. Repeating a call that is already satisfied writes nothing
  (verified live 2026-09-12: the response lists every target under
  `skipped`). Re-asserts from the 5-minute sweep are therefore Modbus-silent
  by construction. No guard templates are needed in the blueprint.
- **Ordered.** Restore before fence before power before command before EMS
  mode; leaving a forced mode writes the EMS mode first; reversing a running
  forced mode writes STOP before the new direction. A sequence that fails
  part-way leaves a sane state.
- **Verified.** After the writes the touched registers are read back and
  compared. The response says `verified: true` only then. With `verify:
  false` the call skips the read-back and re-reads the touched registers
  instead of trusting the cache.
- **Fresh.** The integration polls the settings registers every 60 s. An
  action trusts a snapshot from the last poll, so a call normally makes no
  reads before writing; a stale snapshot is re-read first. Either way the
  plan is made against what the inverter reported, not against what HA
  last asked for.
- **Serialised.** Actions, the settings poll, diagnostics and the raw
  entities share one lock. Two overlapping calls run one after the other;
  a poll never interleaves with a write sequence.
- **Cancellation-proof.** An automation in `mode: restart` cancels its
  in-flight service call when re-triggered. The integration runs every
  control call to its end regardless (writes, read-back, publication), so a
  restart mid-call cannot leave the inverter between two states.
- **Honest afterwards.** `sensor.<device>_battery_mode` and the raw entities
  update from the read-back the moment the call returns, not at the next
  poll. If a write got no answer or the read-back failed, the integration
  re-reads the affected registers and publishes only what the inverter
  answered; if even that fails, the affected entities go unavailable rather
  than showing stale values.
- **Self-healing under contention.** The WiNet-S sometimes answers Modbus
  exception 4 in bursts (seen live with mkaiser still polling). A read-back
  that fails after the writes landed is retried by re-reading and
  re-planning once; if nothing is left to write, the call counts as
  verified.

### 2.3 Response

With `response_variable`, each action returns:

```yaml
action: forced_discharge          # what was applied
writes:                           # in wire order
  - {component: battery_limits, field: max_charge_power, previous: 10, value: 10000}
  - {component: settings, field: forced_power, previous: 1000, value: 2000}
  - {component: settings, field: charge_command, previous: 170, value: 187}
  - {component: settings, field: ems_mode, previous: 0, value: 2}
skipped: [battery_limits.max_discharge_power]   # already right
uncertain: []                     # writes that got no answer
verified: true
battery_mode: forced_discharge    # the sensor's value after the call
```

The blueprint does not need the response; it is useful in traces and for a
future "last action" diagnostic.

### 2.4 Errors

Failures raise HA errors with translated messages. In an automation trace
they read, for example:

- `forced_charge needs a power value.` / `forced_charge power of 12000 W is
  outside 0–10000 W.` (validation, nothing written)
- `settings.ems_mode reads 0 after writing 2; the inverter did not take the
  value.` (verification)
- `Writing settings.forced_power failed: ...` / `No answer while writing
  ...; the value may or may not have landed.`
- `The control call failed: read-back failed: settings` (only after the
  re-plan also failed)

A failed call raises in the automation. Do not swallow it with
`continue_on_error`: the 5-minute sweep re-asserts the action and the
guarded call then writes only what is still missing.

### 2.5 Timing

Measured live 2026-09-12: a write-free call returns in under 0.1 s; a call
with writes takes 1–2 s (writes, a 0.5 s settle, read-back). The inverter
acts within seconds: `hold` took battery power from 5.3 kW charging to
13 W within 20 s; forced modes show in the running state within ~5 s.

## 3. Sensors Numbat may use

- `sensor.<device>_battery_mode` (enum): `self_consumption`,
  `forced_charge`, `forced_discharge`, `no_charge`, `no_discharge`, `hold`,
  `forced_stop`, `external_ems`, `vpp`, `inconsistent`. Attributes:
  `ems_mode`, `charge_command`, `forced_power_w`, `max_charge_power_w`,
  `max_discharge_power_w`, `export_limit_w`, `export_limit_enabled`,
  `pv_limited`, `battery_max_power_w`. `inconsistent` means the registers do
  not add up to any mode (a doubted EMS word, a forced mode with no
  command); it is distinct from HA's `unknown` (not yet polled). The sensor
  refreshes from every action's read-back and every 60 s poll.
- `binary_sensor.<device>_grid_connected` (on Dan's install
  `binary_sensor.sungrow_sh15t_grid_connected`; it keeps that id at M6 since
  mkaiser had no equivalent): on while the running state is not off-grid
  and phase A voltage is present. The blueprint's `grid_sensor` input
  should default to this. Unavailable is treated as connected by the
  blueprint (unchanged).
- The raw entities (`number.*`, `select.*`, `switch.*`) exist for parity with
  mkaiser and for dashboards. The blueprint must not use them: they write
  one register each with no ordering or restore, and `select.<device>_ems_mode`
  uses different option strings from mkaiser's (`self_consumption`,
  `compulsory`, `external_ems`, `vpp`).

## 4. PV limitation: new capability, planner implications

`sungrow.set_pv_limitation {limit: true}` writes the SH-T-only "PV power
limitation" register. Verified on Dan's SH15T:

- PV drops from full output to ~0 W within one 10 s sample (2026-09-11
  morning: 3.5 kW → 3 W; 2026-09-12: 7.07 kW → 12 W in 20 s). MPPT input
  is disconnected, not merely curtailed. **It stops PV charging the battery
  too**: the battery immediately took over the house at 4 kW.
- Restoring (`limit: false`) brings PV back over 40–50 s (MPPT reconnects,
  then ramps). Do not flap it every interval; treat it like a slow actuator
  with a recovery cost.
- The inverter stays in its running state (no stop, no fault); backup is
  unaffected; the setting persists across polls (and, per Dan's earlier
  test, overnight).
- It is orthogonal to the battery mode and the export limit. Combinations
  seen or reasoned, to be confirmed live before Numbat relies on them:
  `pv_off + self_consumption` = the battery serves the house (verified);
  `pv_off + hold` = the grid serves the house with nothing generating;
  `pv_off + forced_charge` = true grid charging with no PV contribution
  (verified by Dan 2026-09-12: the charge was drawn from the grid).

Why Numbat cares: with negative **buy** prices the best plan is to import
for the house (and possibly charge) with PV fully off, not just export
withheld. Today Numbat models curtailment as "export withheld" and
`hold` as "grid serves the house net of PV" — and the optimizer already
*plans* PV off: its PV usage variable is free (`0 ≤ pv_used ≤ pv`, no
"PV serves the load first" constraint), so at negative buy it sets
`pv_used = 0` and imports for the house, which is exactly where `hold`
comes from on those days. The hardware could not follow until now.

So the planner does not need a new decision, only a readout: a `pv_off`
attribute on `sensor.numbat_action` (atomic with the action, like
`curtail`), true when `current_buy < 0` and step 0 has PV available but
uses none (`pv_kw > tol and pv_used_kw < tol`). Gate it on the live buy
price the way `curtail` is gated on the live feed-in price, not on the
solver alone: `pv_used = 0` also appears in cost ties around buy ≈ 0 and
would flap. With the price gate, flapping is bounded by Amber's 30-minute
buy-price blocks, and the 40–50 s recovery per flip is noise at that
cadence. The blueprint calls `set_pv_limitation` from the attribute the
same way it calls `set_export_limit` from `curtail`, and lifts it in the
failsafe. That failsafe is the only thing standing between a dead Numbat
and PV staying cut: an HA alert on `sensor.<device>_battery_mode`'s
`pv_limited` attribute being true for more than ~30 minutes while
`sensor.numbat_status` is not `ok` is cheap insurance. This is planner work
in Numbat; the blueprint side is a few lines once the attribute exists.

A related physics fact, verified 2026-09-12: **a forced discharge cannot
discharge while export is capped and PV exceeds load.** With the export
limit at 50 W, 7.4 kW of PV and a 2.2 kW house, `forced_discharge 2000`
was accepted and verified (running state went to compulsory mode) but the
battery kept charging at 4.9 kW, because the surplus had nowhere else to
go. Numbat already avoids `discharge + curtail`; this confirms the planner
must never expect a discharge to move energy while curtailed with PV up.

## 5. The blueprint rework

New file `blueprints/numbat_actuator_sungrow.yaml`; the generic
`numbat_actuator.yaml` stays for other hardware. Keep everything about
*when* to act (triggers, `numbat_alive`, `grid_connected`, the 1 s settle
delay, `mode: restart`, the 5-minute sweep with its 15-minute re-assert
window, the `curtail` attribute handling) and replace everything about
*how*.

### 5.1 Inputs

Keep: `action_sensor`, `setpoint_sensor`, `status_sensor`,
`max_heartbeat_age_minutes`, `grid_sensor` (describe the integration's
`grid_connected` as the sensor to pick).

Add:

- `sungrow_device`: `selector: {device: {integration: sungrow}}`, required.
- `export_limit_w`: the normal export limit to restore on uncurtail (Dan:
  15000; a DNSP-capped site enters its cap). `selector: {number: {min: 0,
  max: 30000, step: 100, unit_of_measurement: W, mode: box}}`. Required,
  no default.
- `curtail_limit_w`: the cap applied while export is withheld, default 50.
  Not 0: a 0 W feed-in limit makes the SH series hunt (owners report a
  constant 100–150 W import; a 0↔600 W oscillation was seen on Dan's unit
  after a restart), and Dan's automation has used 50 W since the start.

Remove: `charge_actions`, `discharge_actions`, `idle_actions`,
`no_charge_actions`, `hold_actions`, `restore_actions`, `curtail_actions`,
`uncurtail_actions`. The blueprint no longer takes user action sequences.

### 5.2 Actions section (sketch)

```yaml
actions:
  - delay:
      milliseconds: 1000            # keep: coalesces the double-fire
  # export cap is orthogonal to the battery mode: cap before a forced mode
  # could push surplus out, lift it before anything that may export
  - action: sungrow.set_export_limit
    data:
      device_id: !input sungrow_device
      limit_w: "{{ curtail_limit_w if curtail_wanted else export_limit_w }}"
  - action: sungrow.set_battery_mode
    data:
      device_id: !input sungrow_device
      mode: >-
        {%- set forced = {'charge': 'forced_charge', 'discharge': 'forced_discharge'} -%}
        {%- if action in forced and power_w > 0 -%} {{ forced[action] }}
        {%- else -%} {{ {'no_charge': 'no_charge', 'hold': 'hold'}.get(action, 'self_consumption') }}
        {%- endif -%}
      power_w: "{{ power_w }}"
```

Notes:

- `power_w` is ignored for the non-forced modes, so passing it always is
  fine. The forced modes accept `power_w: 0` and are then inert (a forced
  charge at 0 W), which is why the template falls back to
  `self_consumption` when a forced action arrives without a setpoint
  (Numbat publishes `power_w` atomically with the action, so this only
  covers a missing attribute).
- The failsafe path (`numbat_alive` false, grid down, sensors missing)
  yields `action == 'idle'` and `curtail_wanted == false`, so the same two
  calls restore the export limit and self-consumption. No separate
  failsafe branch is needed.
- `curtail` as an action string (older Numbat) maps to `self_consumption`
  with the cap already applied by the first call, as today.
- There is no restore step: `self_consumption`, `forced_*` and `no_charge`
  restore the limits they need themselves.
- If the `pv_off` attribute is added (section 4), a third call goes between
  the two: `sungrow.set_pv_limitation {limit: "{{ pv_off_wanted }}"}`, with
  `pv_off_wanted` defined like `curtail_wanted` (false when Numbat is dead
  or the grid is down, so the failsafe always restores PV), and a third
  attribute trigger on `pv_off`.

### 5.3 Behaviour that changes

- Re-asserts are write-free without any guard templates.
- A lost write is repaired by the sweep exactly as before, and the
  integration's own read-back already catches most of them within the
  call.
- An action call that fails raises; the automation run errors and the
  trace shows the message. Leave it that way. Note this is a change from
  the mkaiser blueprint, where the input sequences ran independently: here
  a failed `set_export_limit` ends the run before `set_battery_mode`. The
  cap-before-forced-mode order is the right one, a transient failure is
  repaired by the next sweep, and the test plan exercises the failure path
  deliberately.
- `mode: restart` is safe: the integration finishes a call the automation
  cancelled.

## 6. Documentation and release chores in the Numbat repo

- `DOCS.md`, "Controlling your inverter": lead with the `sungrow`
  blueprint (two inputs, no sequences); keep the current mkaiser sequences
  under a collapsible "mkaiser YAML package" heading for other users; state
  that `battery.power_convention: charge_negative` stays for the renamed
  `sensor.battery_power`; mention `grid_connected`; describe the `pv_off`
  attribute if implemented.
- `numbat/CHANGELOG.md` `## Unreleased` entry (required by the repo's
  `CLAUDE.md`), README pairing note ("with the sungrow integration the
  actuator is two inputs").
- No add-on code changes are needed for the blueprint alone. Planner changes
  for `pv_off` follow the usual release process (version bump in three
  places, `uv lock`, Dan's review before release).

## 7. Open items for Dan (decide before or during the rework)

1. **Spike boosts vs the power ceiling: decided (Dan, 2026-09-12).** The
   option is the inverter's capability, 15 kW, as mkaiser was configured:
   the 10 kW everyday cap and the 12 kW spike boost apply to forced
   charge/discharge only and are Numbat's to enforce, and self-consumption
   is meant to serve the house at the full 15 kW when it pulls that much.
   The action refuses `power_w` above the option and every non-fenced mode
   restores both limits to it, so no library change is needed. Action item:
   set the option to 15 000 on Dan's install (left at 10 000 during M5).
2. **Whether to implement `pv_off` in the planner now** or ship the
   blueprint first with the two-call shape and add PV limitation later.
3. **`export_limit_w` default.** 15000 matches Dan's inverter; a blueprint
   default should probably be empty (required) so other users enter theirs.
4. The unit-classes of the renamed sensors are pinned by a test in the
   integration repo; if Numbat's `entities` config ever points at other
   sungrow sensors, check `state_class`/units there first.

## 8. Test plan for the new blueprint

Run with the Numbat actuator disabled and mkaiser still loaded (it is only
polling; contention shows as retried exception 4 and is expected):

1. Import the blueprint, create the automation pointing at the sungrow
   device, `export_limit_w` 15000, `grid_sensor` = the integration's
   `grid_connected`. Leave it disabled.
2. Developer Tools → Actions: call `sungrow.set_battery_mode` with each
   mode, check the response, watch `sensor.<device>_battery_mode` follow.
   Repeat a call and see `writes: []`.
3. Enable the automation. Inject Numbat states with Developer Tools (the
   same method used to bench-test the current blueprint): `charge` with
   `power_w`, `discharge`, `idle`, `no_charge`, `hold`, `curtail` attribute
   on and off. Check the trace: two calls per run, write-free re-asserts on
   the 5-minute sweep.
4. Stop the add-on for longer than `max_heartbeat_age_minutes` and confirm
   the failsafe restores self-consumption and the export limit.
5. Make the export call fail once (temporarily set `export_limit_w` above
   the inverter's range, or point the automation at a device that is
   offline) while a forced action is injected: the run must error in the
   trace and the next 5-minute sweep must complete normally once the input
   is fixed. This is the failure path that §5.3 accepts.
6. Disable the automation at the end; confirm the inverter is in
   self-consumption with both limits at 15 000 W (the option) and the
   export limit at its normal value.

Live registers verified on Dan's SH15T so far: every mode transition,
export limit round trip, PV limitation on/off, PV off + forced charge
(grid charging). Not yet verified live: `no_discharge`, start/stop (never
to be used by Numbat).
