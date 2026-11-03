"""Wi-Fi QR code generation (in memory, no files)."""

from __future__ import annotations

import io

import segno

from .api import GuestWifi


def _escape(value: str) -> str:
    """Escape the characters that are special in the WIFI: QR format."""
    for ch in '\\;,:"':
        value = value.replace(ch, "\\" + ch)
    return value


def wifi_qr_payload(guest: GuestWifi) -> str | None:
    """Standard WIFI: payload that phones join by scanning, or None if incomplete."""
    if not guest.ssid or not guest.password:
        return None
    # WPA3-only networks need T:SAE; everything else (WPA2, WPA2/WPA3 mixed) is T:WPA.
    security = "SAE" if guest.psk_version == "sae_only" else "WPA"
    payload = f"WIFI:T:{security};S:{_escape(guest.ssid)};P:{_escape(guest.password)};"
    if guest.hidden:
        payload += "H:true;"
    return payload + ";"


def render_png(payload: str) -> bytes:
    """Render the payload as a PNG and return its bytes. CPU-bound; run in executor."""
    buf = io.BytesIO()
    segno.make_qr(payload, error="m").save(buf, kind="png", scale=10, border=4)
    return buf.getvalue()
