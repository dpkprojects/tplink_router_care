"""Constants for the TP-Link Router Care integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "tplink_router_care"

CONF_CLIENT_CLASS: Final = "client_class"

DEFAULT_USERNAME: Final = "admin"
# LuCI wireless form for the 5 GHz guest network (admin/wireless?form=...).
GUEST_5G_FORM: Final = "guest_5g"
# Shared-key form used by routers where 2.4 + 5 GHz guest use one password.
GUEST_2G5G_FORM: Final = "guest_2g5g"
GUEST_PASSWORD_LENGTH: Final = 8
DEFAULT_SCAN_INTERVAL: Final = timedelta(hours=1)

# Per-HTTP-request timeout handed to the library (seconds).
REQUEST_TIMEOUT: Final = 10
# Hard ceiling for one full poll cycle (login + fetch + logout).
UPDATE_TIMEOUT: Final = 45
# How long a worker thread waits for the router lock before giving up. If an
# earlier call timed out on the HA side, its thread may still be talking to the
# router; later calls must not pile up behind it. Keep below UPDATE_TIMEOUT so
# the waiting thread exits before HA stops waiting for it.
LOCK_TIMEOUT: Final = 30
# Hard ceiling for router model auto-detection in the config flow. The library
# probes ~30 client classes one after another, so this needs headroom.
DETECT_TIMEOUT: Final = 180
# TCP reachability pre-check before the (slow) auto-detection.
REACHABILITY_TIMEOUT: Final = 5
