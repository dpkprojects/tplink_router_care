"""Constants for the TP-Link Router Care integration."""

from datetime import timedelta
from typing import Final

DOMAIN: Final = "tplink_router_care"

CONF_CLIENT_CLASS: Final = "client_class"
# Options
CONF_QR_THEME: Final = "qr_theme"
QR_THEMES: Final = ["light", "dark"]
DEFAULT_QR_THEME: Final = "light"
# Webhook serving the QR card PNG (its ID is stored under CONF_WEBHOOK_ID).
CONF_WEBHOOK_ENABLED: Final = "webhook_enabled"
# Off by default: the card shows the guest password.
DEFAULT_WEBHOOK_ENABLED: Final = False

DEFAULT_USERNAME: Final = "admin"
# LuCI wireless form for the 5 GHz guest network (admin/wireless?form=...).
GUEST_5G_FORM: Final = "guest_5g"
# Shared-key form used by routers where 2.4 + 5 GHz guest use one password.
GUEST_2G5G_FORM: Final = "guest_2g5g"
GUEST_PASSWORD_LENGTH: Final = 8
# Polling interval option, in minutes (strings, as the select selector needs).
# "0" turns automatic polling off; the Refresh data button still works.
SCAN_INTERVAL_OPTIONS: Final = ["0", "30", "60", "180", "360", "720", "1440", "10080"]
DEFAULT_SCAN_INTERVAL: Final = "60"
# Automatic guest Wi-Fi password (and QR code) rotation, in hours. "0" = off;
# then 12 h, 1 day, 2 days, 1 week, 1 month (30 days), 3 months (90 days).
CONF_GUEST_ROTATION: Final = "guest_password_rotation"
GUEST_ROTATION_OPTIONS: Final = ["0", "12", "24", "48", "168", "720", "2160"]
DEFAULT_GUEST_ROTATION: Final = "0"
# Entry data key holding when the guest password was last changed by us (ISO).
DATA_GUEST_ROTATED_AT: Final = "guest_password_rotated_at"
# A due rotation never fires sooner than this (e.g. right after startup).
ROTATION_MIN_DELAY: Final = timedelta(minutes=2)
# Wait before retrying a failed automatic rotation.
ROTATION_RETRY: Final = timedelta(hours=1)

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
