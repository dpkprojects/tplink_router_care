"""Wi-Fi QR card generation (in memory, no files).

Renders a "Wi-Fi card" PNG: a header with a Wi-Fi icon, a scannable QR code,
and the network name and password underneath. Pillow ships with Home Assistant
core, so it is not listed in manifest.json requirements.
"""

from __future__ import annotations

from functools import lru_cache
import io

from PIL import Image, ImageDraw, ImageFont
import segno

from .api import GuestWifi

# Layout, in final pixels. Everything except the QR code is drawn at SS x size
# and downscaled for smooth edges; the QR is pasted afterwards at an exact
# integer scale so its modules stay perfectly sharp for scanning.
SS = 3
WIDTH = 640
PAD = 40
RADIUS = 36
HEADER_H = 128
QR_TARGET = 420

ACCENT = (37, 99, 235)  # blue
ACCENT_DARK = (29, 78, 216)
CARD_BG = (255, 255, 255)
PANEL_BG = (243, 246, 251)
TEXT = (17, 24, 39)
MUTED = (107, 114, 128)
WHITE = (255, 255, 255)


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


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Pillow's bundled scalable font (falls back to bitmap without FreeType)."""
    return ImageFont.load_default(size)


def _text_w(draw: ImageDraw.ImageDraw, text: str, size: int, stroke: int = 0) -> int:
    left, _, right, _ = draw.textbbox((0, 0), text, font=_font(size), stroke_width=stroke)
    return int(right - left)


def _fit_lines(
    draw: ImageDraw.ImageDraw, text: str, max_w: int, size: int, min_size: int, stroke: int
) -> tuple[list[str], int]:
    """Shrink the font until text fits on one line; below min_size, wrap by characters."""
    while size > min_size and _text_w(draw, text, size, stroke) > max_w:
        size -= SS
    if _text_w(draw, text, size, stroke) <= max_w:
        return [text], size
    lines, line = [], ""
    for ch in text:
        if line and _text_w(draw, line + ch, size, stroke) > max_w:
            lines.append(line)
            line = ch
        else:
            line += ch
    lines.append(line)
    return lines, size


def _wifi_icon(draw: ImageDraw.ImageDraw, cx: int, cy: int, size: int, color) -> None:
    """Classic Wi-Fi symbol: a dot with three arcs above it. (cx, cy) = dot center."""
    width = max(SS, size // 9)
    for i in (1, 2, 3):
        r = size * i // 3
        draw.arc((cx - r, cy - r, cx + r, cy + r), 225, 315, fill=color, width=width)
    d = width
    draw.ellipse((cx - d, cy - d, cx + d, cy + d), fill=color)


def _qr_image(payload: str) -> Image.Image:
    qr = segno.make_qr(payload, error="m")
    modules = qr.symbol_size(scale=1, border=2)[0]
    scale = max(4, QR_TARGET // modules)
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale, border=2, dark=TEXT, light=CARD_BG)
    buf.seek(0)
    return Image.open(buf).convert("RGBA")


def render_wifi_card(guest: GuestWifi, title: str = "Guest Wi-Fi") -> bytes | None:
    """Render the Wi-Fi card PNG. CPU-bound; run in the executor."""
    payload = wifi_qr_payload(guest)
    if payload is None:
        return None
    assert guest.ssid is not None and guest.password is not None
    qr = _qr_image(payload)

    # Measure text at supersampled size to compute the card height.
    w = WIDTH * SS
    pad = PAD * SS
    inner_w = w - 2 * pad - 2 * 28 * SS  # panel has 28px inner padding
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    stroke = SS  # faux-bold for values
    ssid_lines, ssid_size = _fit_lines(probe, guest.ssid, inner_w, 38 * SS, 22 * SS, stroke)
    pw_lines, pw_size = _fit_lines(probe, guest.password, inner_w, 38 * SS, 22 * SS, stroke)

    label_size = 17 * SS
    row_gap = 10 * SS

    def block_h(lines: list[str], size: int) -> int:
        return label_size + row_gap + len(lines) * int(size * 1.25)

    qr_top = (HEADER_H + 36) * SS
    caption_top = qr_top + qr.height * SS + 18 * SS
    caption_size = 20 * SS
    panel_top = caption_top + caption_size + 30 * SS
    panel_pad = 26 * SS
    panel_h = (
        panel_pad
        + block_h(ssid_lines, ssid_size)
        + 24 * SS
        + block_h(pw_lines, pw_size)
        + panel_pad
    )
    h = panel_top + panel_h + pad

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = RADIUS * SS

    # Card and header band (rounded top, square bottom edge).
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=CARD_BG)
    draw.rounded_rectangle((0, 0, w - 1, HEADER_H * SS), radius=radius, fill=ACCENT)
    draw.rectangle((0, HEADER_H * SS - radius, w - 1, HEADER_H * SS), fill=ACCENT)

    # Header: Wi-Fi icon + title + subtitle.
    icon = 30 * SS
    icon_cx = pad + icon
    _wifi_icon(draw, icon_cx, (HEADER_H * SS) // 2 + icon // 2, icon, WHITE)
    tx = icon_cx + icon + 24 * SS
    title_size = 40 * SS
    draw.text((tx, 30 * SS), title, font=_font(title_size), fill=WHITE, stroke_width=SS,
              stroke_fill=WHITE)
    sub = "5 GHz" + (" · Hidden network" if guest.hidden else "")
    draw.text((tx, 30 * SS + title_size + 8 * SS), sub, font=_font(20 * SS),
              fill=(219, 234, 254))

    # Caption under the QR.
    caption = "Scan with your phone camera to join"
    cw = _text_w(draw, caption, caption_size)
    draw.text(((w - cw) // 2, caption_top), caption, font=_font(caption_size), fill=MUTED)

    # Info panel with network name and password.
    draw.rounded_rectangle(
        (pad, panel_top, w - pad, panel_top + panel_h), radius=20 * SS, fill=PANEL_BG
    )
    x = pad + 28 * SS
    y = panel_top + panel_pad
    for label, lines, size in (
        ("NETWORK", ssid_lines, ssid_size),
        ("PASSWORD", pw_lines, pw_size),
    ):
        draw.text((x, y), label, font=_font(label_size), fill=ACCENT_DARK, stroke_width=1,
                  stroke_fill=ACCENT_DARK)
        y += label_size + row_gap
        for line in lines:
            draw.text((x, y), line, font=_font(size), fill=TEXT, stroke_width=stroke // 2,
                      stroke_fill=TEXT)
            y += int(size * 1.25)
        y += 24 * SS

    # Downscale for anti-aliasing, then paste the QR at native resolution.
    card = img.resize((WIDTH, h // SS), Image.Resampling.LANCZOS)
    card.paste(qr, ((WIDTH - qr.width) // 2, qr_top // SS))

    out = io.BytesIO()
    card.save(out, format="PNG", optimize=True)
    return out.getvalue()
