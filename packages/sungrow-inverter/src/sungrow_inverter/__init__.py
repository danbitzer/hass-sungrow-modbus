"""sungrow-inverter — Sungrow SH-T hybrid inverters over Modbus.

Construct ``SungrowInverter(unit)`` with a ``modbus_connection.ModbusUnit``,
await an update method, then read sub-systems as plain Python objects.

Addresses throughout are Modbus protocol addresses: one below the register
number in Sungrow's document (``address 4989  # reg 4990``).
"""

__version__ = "0.1.0a1"

__all__ = ["__version__"]
