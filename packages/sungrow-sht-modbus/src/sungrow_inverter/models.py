"""The SH-T model gate: which device type codes this library serves."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .exceptions import UnsupportedModelError


@dataclass(frozen=True)
class ShtModel:
    """One SH-T inverter model, per Appendix 1 of the Sungrow protocol."""

    code: int
    name: str
    mppt: int
    strings: tuple[int, ...]
    """Strings per MPPT, in MPPT order."""


SHT_MODELS: Mapping[int, ShtModel] = {
    0x0E20: ShtModel(0x0E20, "SH5T", 2, (1, 1)),
    0x0E21: ShtModel(0x0E21, "SH6T", 2, (1, 1)),
    0x0E22: ShtModel(0x0E22, "SH8T", 2, (1, 1)),
    0x0E23: ShtModel(0x0E23, "SH10T", 2, (2, 1)),
    0x0E24: ShtModel(0x0E24, "SH12T", 2, (2, 1)),
    0x0E25: ShtModel(0x0E25, "SH15T", 3, (2, 2, 1)),
    0x0E26: ShtModel(0x0E26, "SH20T", 3, (2, 2, 1)),
    0x0E28: ShtModel(0x0E28, "SH25T", 3, (2, 2, 1)),
}
"""Device type code (reg 5000) to model. 0x0E27 is unassigned."""

OTHER_MODELS: Mapping[int, str] = {
    # older single-phase hybrids (mkaiser's table)
    0x0D06: "SH3K6",
    0x0D07: "SH4K6",
    0x0D09: "SH5K-20",
    0x0D03: "SH5K-V13",
    0x0D0A: "SH3K6-30",
    0x0D0B: "SH4K6-30",
    0x0D0C: "SH5K-30",
    # SH3.0-6.0RS / SH8.0-10RS
    0x0D17: "SH3.0RS",
    0x0D0D: "SH3.6RS",
    0x0D18: "SH4.0RS",
    0x0D0F: "SH5.0RS",
    0x0D10: "SH6.0RS",
    0x0D1A: "SH8.0RS",
    0x0D1B: "SH10RS",
    # SH5.0-10RT and variants
    0x0E00: "SH5.0RT",
    0x0E01: "SH6.0RT",
    0x0E02: "SH8.0RT",
    0x0E03: "SH10RT",
    0x0E10: "SH5.0RT-20",
    0x0E11: "SH6.0RT-20",
    0x0E12: "SH8.0RT-20",
    0x0E13: "SH10RT-20",
    0x0E0C: "SH5.0RT-V112",
    0x0E0D: "SH6.0RT-V112",
    0x0E0E: "SH8.0RT-V112",
    0x0E0F: "SH10RT-V112",
    0x0E08: "SH5.0RT-V122",
    0x0E09: "SH6.0RT-V122",
    0x0E0A: "SH8.0RT-V122",
    0x0E0B: "SH10RT-V122",
    # MG5-10RL / SH5-10RL
    0x0D27: "MG5RL",
    0x0D28: "MG6RL",
    0x0D31: "MG7.5RL",
    0x0D29: "MG8RL",
    0x0D2A: "MG10RL",
    0x0D2F: "MG12RL",
    0x0D2B: "SH5RL",
    0x0D2C: "SH6RL",
    0x0D2D: "SH8RL",
    0x0D2E: "SH10RL",
    # SH50~125CX
    0x0E51: "SH50CX",
    0x0E52: "SH80CX",
    0x0E39: "SH100CX",
    0x0E3A: "SH110CX",
    0x0E3D: "SH125CX",
}
"""Known non-SH-T device type codes, used only to name a rejected model."""


def model_for(code: int) -> ShtModel:
    """Return the SH-T model for a device type code.

    Raises ``UnsupportedModelError`` for any other code, naming the model
    when it is a known non-SH-T Sungrow inverter.
    """
    model = SHT_MODELS.get(code)
    if model is None:
        raise UnsupportedModelError(code, OTHER_MODELS.get(code))
    return model
