# sungrow-inverter

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
