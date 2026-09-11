"""Constants for the Sungrow SH-T integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "sungrow"
MANUFACTURER = "Sungrow"

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]

# Entry data (from the config flow's probe).
CONF_UNIT_ID = "unit_id"
CONF_DEVICE_TYPE_CODE = "device_type_code"
CONF_SERIAL = "serial"
CONF_NOMINAL_POWER_W = "nominal_power_w"
CONF_BDC_RATED_POWER_W = "bdc_rated_power_w"

# Options.
CONF_REALTIME_INTERVAL = "realtime_interval"
CONF_SETTINGS_INTERVAL = "settings_interval"
CONF_BATTERY_MAX_POWER_W = "battery_max_power_w"
CONF_MESSAGE_SPACING_MS = "message_spacing_ms"
CONF_INCLUDE_REGISTER_DUMP = "include_register_dump"

DEFAULT_PORT = 502
DEFAULT_UNIT_ID = 1
DEFAULT_REALTIME_INTERVAL = 10
DEFAULT_SETTINGS_INTERVAL = 60
DEFAULT_MESSAGE_SPACING_MS = 50
MIN_MESSAGE_SPACING_MS = 10
"""Never 0: with no spacing the shared unit does not serialise a poll
against an action's write sequence."""

REALTIME = "realtime"
SETTINGS = "settings"

SERIAL_ADDRESS = 4989
SERIAL_WORDS = 10
"""Input registers holding the serial number, dropped from diagnostics."""
