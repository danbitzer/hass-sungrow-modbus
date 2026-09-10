"""Field helpers and write validators specific to Sungrow's register style."""

from __future__ import annotations

from typing import Any

from modbus_connection.model import NumberField

from .const import AA, U16_NAN, X55

_AA55: dict[int, bool] = {AA: True, X55: False}


def _encode_aa55(value: Any) -> int:
    """Map a bool to the 0xAA / 0x55 word Sungrow's enable registers take."""
    if not isinstance(value, bool):
        raise ValueError(f"expected a bool, got {value!r}")
    return AA if value else X55


def aa55(address: int, *, writable: bool = True) -> NumberField[bool]:
    """A Sungrow enable register: 0xAA reads True, 0x55 False, anything else None.

    Writing takes a bool and puts the matching word on the wire.
    """
    return NumberField(
        address,
        signed=False,
        convert=_AA55,
        nan=U16_NAN,
        writable=_encode_aa55 if writable else False,
    )


# -- write validators ----------------------------------------------------------
# Each receives the engineering value a caller asked to write and returns the
# value to encode, or raises ``ValueError``.


def non_negative_u16(value: Any) -> int:
    """A whole number of watts (or percent) that fits an unsigned register."""
    number = int(value)
    if number != value or not 0 <= number <= 0xFFFF:
        raise ValueError(f"{value!r} is not a whole number in 0..65535")
    return number


def soc_upper(value: Any) -> float:
    """Max. SoC (reg 13058): 50.0 to 100.0 %."""
    number = float(value)
    if not 50.0 <= number <= 100.0:
        raise ValueError(f"max SoC {value!r} is outside 50.0..100.0 %")
    return number


def soc_lower(value: Any) -> float:
    """Min. SoC (reg 13059): 0.0 to 50.0 %."""
    number = float(value)
    if not 0.0 <= number <= 50.0:
        raise ValueError(f"min SoC {value!r} is outside 0.0..50.0 %")
    return number


def ratio(value: Any) -> float:
    """A limit ratio (regs 13088 / 13090): 0.0 to 100.0 %."""
    number = float(value)
    if not 0.0 <= number <= 100.0:
        raise ValueError(f"ratio {value!r} is outside 0.0..100.0 %")
    return number


def percent(value: Any) -> int:
    """A whole percentage, 0 to 100."""
    number = int(value)
    if number != value or not 0 <= number <= 100:
        raise ValueError(f"{value!r} is not a whole percentage in 0..100")
    return number


def multiple_of_10(value: Any) -> int:
    """A power in watts on a 0.01 kW register: non-negative, whole tens."""
    number = int(value)
    if number != value or number < 0 or number % 10:
        raise ValueError(f"{value!r} is not a non-negative multiple of 10 W")
    if number > 10 * 0xFFFF:
        raise ValueError(f"{value!r} W does not fit the register")
    return number
