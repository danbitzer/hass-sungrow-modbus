"""Constants for the Sungrow SH-T integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "sungrow"
MANUFACTURER = "Sungrow"

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

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

# Actions.
SERVICE_SET_BATTERY_MODE = "set_battery_mode"
SERVICE_SET_EXPORT_LIMIT = "set_export_limit"
SERVICE_SET_PV_LIMITATION = "set_pv_limitation"
SERVICE_START_INVERTER = "start_inverter"
SERVICE_STOP_INVERTER = "stop_inverter"
ATTR_MODE = "mode"
ATTR_POWER_W = "power_w"
ATTR_VERIFY = "verify"
ATTR_LIMIT_W = "limit_w"
ATTR_ENABLED = "enabled"
ATTR_LIMIT = "limit"

FRESHNESS_MARGIN_S = 5
"""Added to the settings poll interval: a snapshot from the last poll is
fresh enough for a control call, so it does not re-read first."""
