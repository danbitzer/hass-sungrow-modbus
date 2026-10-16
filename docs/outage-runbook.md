# Outage runbook: when the inverter does not provide backup

For a Sungrow SH-T hybrid with an SBR battery and a WiNet-S2 dongle, driven by
the `sungrow` integration. Written after two outages (Oct 2026) in which the
inverter sat in **fault 329** ("AC port wiring anomaly / phase voltage
issue") instead of switching to backup. Nothing here needs HA or the
router; both are usually dark in an outage.

Switch names below are the labels on the switchboard and next to the
inverter: **Grid isolator** (the main grid switch), **Inverter isolator**
(the inverter's AC side), **DC isolator** (the PV strings). The battery's
own control is the **power button** on the top module of the SBR stack.

## 0. Thirty-second triage

1. Look at the inverter's front LED bar.
   - **Dark** → the inverter is off. Go to step 2 (black start).
   - **Lit, red or flashing** → it is in fault. Go to step 1.
   - **Lit normally but no backup power** → go to step 1 as well; the
     settings may be fencing the battery.
2. Look at the WiNet-S2 dongle under the inverter. Dark LEDs mean the
   inverter is off (it powers the dongle). A flashing WLAN LED means it is
   trying to reach the router, which is normal in an outage.

## 1. Clear a fault by making the outage "clean"

The inverter only switches to backup when it sees all three phases gone.
A brownout or a single phase dropping reads as an AC anomaly (fault 329)
and it stays in fault. Isolating the grid turns that into a clean outage.

1. Switch the **Grid isolator** OFF.
2. Wait two full minutes. Watch the inverter LED and the backup circuit.
   If power returns, stop here and skip to step 4.
3. If still faulted, try a **soft restart** before touching the battery:
   wake the dongle's hotspot (section 3), open Local Access, and use the
   inverter's **Stop** command, wait thirty seconds, then **Start**. This
   reboots the control software with the battery untouched and clears most
   latched faults. Allow two minutes.
4. Only if the fault survives both of the above, do a **hard power cycle**
   with the grid still isolated. The inverter's electronics run from the
   battery's DC link, so without switching the battery off it never
   actually restarts:
   1. **Inverter isolator** OFF.
   2. **DC isolator** OFF.
   3. Battery OFF: press and hold the SBR power button until its LED goes
      out. If the battery has its own breaker, switch that off too.
   4. Wait one minute. The inverter LEDs should be dark.
   5. Battery ON first: press and hold the power button until its LED
      lights. The battery must be live before the inverter, or it cannot
      start without the grid.
   6. **DC isolator** ON.
   7. **Inverter isolator** ON.
   8. Leave the **Grid isolator** OFF. Wait up to three minutes for the
      inverter to boot; it then runs the backup circuit from battery and PV.
5. When the grid is back (a neighbour's lights, the street), switch the
   **Grid isolator** ON. The inverter detects the grid, synchronises and
   reconnects by itself within about a minute; the backup circuit stays up
   through the handover.

If step 4 leaves the inverter in fault with the grid isolated, the cause
is internal or in the wiring (loose AC terminal, swapped backup/grid
terminals are the documented ones for 329). Nothing you can switch will
help; restore the Grid isolator and call the installer or Sungrow.

## 2. Black start when the inverter is off

With no grid and the inverter dark, the battery has gone to sleep and the
inverter will not wake it. Follow step 4 above from 4.5 onwards: battery
ON, DC ON, Inverter isolator ON, Grid isolator OFF, wait three minutes.

## 3. Checking settings without the router

If the inverter runs but backup still does not come, the battery may be
fenced by an automation that could not revert (Numbat's `hold` or
`no_discharge`, or a forced mode) or PV may be cut.

1. On the dongle, **press the button three times** to wake its Wi-Fi
   hotspot. Within a minute a network `SG-<dongle serial>` appears. (One
   press enables Bluetooth for the app instead.)
2. Join it with a phone; no internet is expected.
3. iSolarCloud app → **Local Access** → log in `user` / `pw1111`
   (`admin` / `pw8888` for installer settings) unless the installer
   changed them. The web page at `http://11.11.11.1` shows the same.
4. Read the running state, battery SoC and any fault code. Note the code
   and the time for the installer.
5. Under energy management: **EMS mode = Self-consumption**, **max
   charging power = 15000 W**, **max discharging power = 15000 W**,
   **PV power limitation = allow**, **backup / off-grid option = enabled**,
   reserve SoC 5 %. Save.
6. Backup should engage within seconds if the battery is above the
   reserve.

The dongle's hotspot sleeps again after a while; press three times again
if it disappears.

## 4. After power returns

1. HA comes back on its own; the integration reconnects when the dongle
   rejoins the router (a few minutes). The actuator resumes.
2. Check `sensor.<device>_battery_mode` reads `self_consumption` with both
   limits at 15000 and PV limitation off; if you changed settings in the
   app, the integration's next poll shows them.
3. Write down: the time the grid dropped, what the house showed (all dark
   or some circuits live), the fault code, and whether any of the steps
   above restored backup. Download the integration's diagnostics
   (Settings → Devices → Sungrow → Download diagnostics); it carries every
   alarm word.

## 5. What to test on a normal day

Opening the **Grid isolator** on a normal day is a clean simulated outage
and shows in five minutes whether backup engages at all. Do it twice:
once in the evening with the battery partly discharged and no PV (the
easy case), and once around midday with the battery at 100 % and PV high
(the case in which the Oct 2026 outage found the inverter). Close the
isolator afterwards; the inverter resynchronises on its own.

## Known exposure

`hold`, `no_discharge` and the forced modes fence or override the
battery, and PV limitation cuts generation. Any of them active at the
moment of an outage disarms backup, and with HA on mains nothing can
revert them until power returns. Either keep HA (and the router) on the
backup circuit so the actuator can restore self-consumption when it sees
the grid go, or have the planner avoid those states when backup matters.
