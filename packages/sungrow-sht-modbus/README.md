# sungrow-sht-modbus

Asynchronous, transport-independent Python library for Sungrow **SH-T** hybrid
inverters (SH5T–SH25T) over Modbus, built on
[modbus-connection](https://github.com/home-assistant-libs/modbus-connection).

The library does not own a connection: construct `SungrowInverter(unit)` with a
`modbus_connection.ModbusUnit` from any backend (tmodbus, pymodbus, or the
in-memory mock), call the update methods, and read typed attributes.

It is the backend of the `sungrow` Home Assistant integration in the same
repository, and has no Home Assistant dependency.

Register map derived from Sungrow's *Communication Protocol of Residential and
Small Industrial Hybrid Inverter* and from
[mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant](https://github.com/mkaiser/Sungrow-SHx-Inverter-Modbus-Home-Assistant)
(MIT) — see `NOTICE.md` at the repository root.

## Usage

```python
from modbus_connection import ModbusTcpParams
from modbus_connection.tmodbus import ModbusConnection

from sungrow_inverter import RetryingUnit, SungrowInverter

connection = ModbusConnection(ModbusTcpParams(host="192.168.1.50", port=502))
try:
    # A WiNet-S answers Modbus exception 4 now and then under contention;
    # RetryingUnit retries those (and timeouts), never real refusals.
    unit = RetryingUnit(connection.for_unit(1))

    probe = await SungrowInverter.async_probe(unit)  # identity only; gates on SH-T
    inverter = SungrowInverter(unit, model=probe.model, battery_max_power_w=12000)

    await inverter.async_update_realtime()  # measurements: poll every ~10 s
    await inverter.async_update_settings()  # settings, limits, energy, alarms: ~60 s

    print(inverter.flows.running_state, inverter.flows.power_flow)
    print(inverter.battery.battery_level, inverter.battery_power.battery_power)
    print(inverter.settings.ems_mode, inverter.battery_limits.max_charge_power)
finally:
    await connection.close()
```

- `async_probe` raises `UnsupportedModelError` for any non-SH-T device type
  code, naming the model when it is a known Sungrow inverter.
- The first update reads the identity block, restricts the MPPT 3 fields on
  two-MPPT models, probes the optional blocks (firmware strings, alarms, start
  power, the APL shadow) and fixes the poll lists. Nothing is written.
- Each update returns an `UpdateReport`: which components refreshed and which
  failed with what. A connection loss is raised; one refused block only marks
  its component failed.
- Addresses are protocol addresses (register − 1); 32-bit values use the low
  word first; `battery_power` is positive when discharging.
- `async_read_raw()` returns the raw register map for diagnostics; the mock
  backend's `load_raw()` replays it.

`scripts/query.py` reads a real inverter once and prints everything:

```sh
uv run --package sungrow-sht-modbus python packages/sungrow-sht-modbus/scripts/query.py \
    "$SUNGROW_HOST" --unit "${SUNGROW_UNIT:-1}" --raw .testdata/raw.json
```

The `--raw` dump replaces the serial number with `A123456789` unless
`--keep-serial` is passed. Never commit a dump that carries a real serial.
