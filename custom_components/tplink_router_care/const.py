"""Constants for the TP-Link Router Care integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "tplink_router_care"

CONF_CLIENT_CLASS: Final = "client_class"

DEFAULT_USERNAME: Final = "admin"
DEFAULT_SCAN_INTERVAL: Final = timedelta(seconds=60)

# Per-HTTP-request timeout handed to the library (seconds).
REQUEST_TIMEOUT: Final = 10
# Hard ceiling for one full poll cycle (login + fetch + logout).
UPDATE_TIMEOUT: Final = 45
# Hard ceiling for router model auto-detection in the config flow. The library
# probes ~30 client classes one after another, so this needs headroom.
DETECT_TIMEOUT: Final = 180
# TCP reachability pre-check before the (slow) auto-detection.
REACHABILITY_TIMEOUT: Final = 5
