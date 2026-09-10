"""The Sungrow SH-T hybrid inverter integration.

Skeleton (M0): the config flow, coordinators and platforms arrive in later
milestones. See PLAN.md at the repository root.
"""

from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

__all__ = ["DOMAIN"]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration (nothing to do until config entries exist)."""
    return True
