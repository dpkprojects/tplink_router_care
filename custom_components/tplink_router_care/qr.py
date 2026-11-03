"""Wi-Fi QR card generation (in memory, no files).

Renders a landscape "Wi-Fi card" PNG: a header with a Wi-Fi icon, a scannable
QR code on the left, and the network name and password on the right. Pillow
ships with Home Assistant core, so it is not listed in manifest.json
requirements.
"""

from __future__ import annotations

from functools import lru_cache
import io

from PIL import Image, ImageDraw, ImageFilter, ImageFont
import segno

from .api import GuestWifi

# Layout, in final pixels. Everything except the QR code is drawn at SS x size
# and downscaled for smooth edges; the QR is pasted afterwards at an exact
# integer scale so its modules stay perfectly sharp for scanning.
SS = 3
WIDTH = 1040
HEIGHT = 529  # fixed, so the card never changes size on a dashboard
PAD = 34
GAP = 38  # between QR and info panel
RADIUS = 34
HEADER_H = 100
BODY_H = HEIGHT - HEADER_H - 2 * PAD  # QR and panel area height
PANEL_PAD = 32

ACCENT = (37, 99, 235)  # blue
ACCENT_DARK = (29, 78, 216)
CARD_BG = (255, 255, 255)
PANEL_BG = (243, 246, 251)
DIVIDER = (219, 225, 234)
TEXT = (17, 24, 39)
MUTED = (107, 114, 128)
WHITE = (255, 255, 255)
# Wi-Fi glyph in the QR center: vertical gradient, blue-teal top -> green-teal bottom.
GLYPH_TOP = (8, 145, 178)  # cyan-600
GLYPH_BOTTOM = (13, 148, 136)  # teal-600


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


def _wrap_words(draw: ImageDraw.ImageDraw, text: str, max_w: int, size: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        candidate = f"{line} {word}".strip()
        if line and _text_w(draw, candidate, size) > max_w:
            lines.append(line)
            line = word
        else:
            line = candidate
    lines.append(line)
    return lines


def _wifi_glyph_mask(box: int) -> Image.Image:
    """iOS-style Wi-Fi glyph as an L mask (255 = ink), ``box`` x ``box`` pixels.

    A 90-degree fan: a solid wedge at the bottom plus two arc bands, with
    softly rounded corners. The glyph is centered in the box.
    """
    mask = Image.new("L", (box, box), 0)
    draw = ImageDraw.Draw(mask)
    # Outer radius: the fan is 1.41 * s wide, so 0.56 leaves a clear margin
    # inside the round clearance area on every side.
    s = int(box * 0.56)
    cx = box // 2
    cy = box // 2 + s // 2  # wedge apex; puts the glyph's vertical middle at center
    wedge = round(s * 0.34)
    draw.pieslice((cx - wedge, cy - wedge, cx + wedge, cy + wedge), 225, 315, fill=255)
    for inner, outer in ((0.47, 0.67), (0.80, 1.0)):
        r = round(s * outer)
        draw.arc(
            (cx - r, cy - r, cx + r, cy + r), 225, 315, fill=255, width=round(s * (outer - inner))
        )
    # Round every corner: blur, then re-threshold.
    soften = max(1, round(s * 0.045))
    return mask.filter(ImageFilter.GaussianBlur(soften)).point(lambda v: 255 if v >= 128 else 0)


def _logo_badge(size: int) -> Image.Image:
    """Wi-Fi glyph for the QR center, no colored background.

    The circle behind it is the QR's own light color, so it just reads as a
    gap in the code; it's needed so modules don't touch the glyph.
    """
    big = size * SS
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(img).ellipse((0, 0, big - 1, big - 1), fill=CARD_BG)
    glyph = _wifi_glyph_mask(big)
    img.paste(_vertical_gradient(big, glyph.getbbox()), (0, 0), glyph)
    return img.resize((size, size), Image.Resampling.LANCZOS)


def _vertical_gradient(box: int, bbox: tuple[int, int, int, int] | None) -> Image.Image:
    """box x box RGBA fill going GLYPH_TOP -> GLYPH_BOTTOM across the glyph's height."""
    top, bottom = (bbox[1], bbox[3]) if bbox else (0, box)
    span = max(1, bottom - top)
    ramp = Image.linear_gradient("L").resize((1, span))  # 0 at top -> 255 at bottom
    mask = Image.new("L", (1, box), 0)
    mask.paste(ramp, (0, top))
    mask.paste(255, (0, bottom, 1, box))
    mask = mask.resize((box, box))
    return Image.composite(
        Image.new("RGBA", (box, box), (*GLYPH_BOTTOM, 255)),
        Image.new("RGBA", (box, box), (*GLYPH_TOP, 255)),
        mask,
    )


def _qr_image(payload: str, max_px: int) -> Image.Image:
    """QR code (at most max_px square) with a Wi-Fi glyph in the middle.

    Uses the highest error correction level (H, ~30% recoverable) so the glyph,
    which covers well under 10% of the symbol, never stops it from scanning.
    The QR gets a 1-module border so the modules can be as large as possible;
    the white card around it completes the quiet zone (PAD is several modules
    wide). Tested to decode best of the H/Q x border 1/2/4 combinations.
    """
    qr = segno.make_qr(payload, error="h")
    border = 1
    modules = qr.symbol_size(scale=1, border=border)[0]
    scale = max(3, max_px // modules)
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale, border=border, dark=TEXT, light=CARD_BG)
    buf.seek(0)
    img = Image.open(buf).convert("RGBA")

    symbol_px = (modules - 2 * border) * scale
    badge = _logo_badge(int(symbol_px * 0.24))
    offset = ((img.width - badge.width) // 2, (img.height - badge.height) // 2)
    img.alpha_composite(badge, offset)
    return img


def render_wifi_card(guest: GuestWifi, title: str = "Scan to Connect") -> bytes | None:
    """Render the landscape Wi-Fi card PNG (WIDTH x HEIGHT). CPU-bound; run in executor."""
    payload = wifi_qr_payload(guest)
    if payload is None:
        return None
    assert guest.ssid is not None and guest.password is not None
    qr = _qr_image(payload, BODY_H)

    # Geometry at supersampled scale.
    w, h = WIDTH * SS, HEIGHT * SS
    pad = PAD * SS
    header_h = HEADER_H * SS
    body_top = header_h + pad
    body_h = BODY_H * SS
    qr_size = qr.width * SS
    panel_x = pad + qr_size + GAP * SS
    panel_w = w - pad - panel_x
    panel_pad = PANEL_PAD * SS
    inner_w = panel_w - 2 * panel_pad

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    stroke = SS  # faux-bold for values
    label_size = 16 * SS
    label_gap = 10 * SS
    block_gap = 24 * SS
    caption_size = 18 * SS
    caption_lines = _wrap_words(
        probe, "Open your phone camera and point it at the code", inner_w, caption_size
    )

    def block_h(lines: list[str], size: int) -> int:
        return label_size + label_gap + len(lines) * int(size * 1.22)

    # Largest value font that fits the fixed panel height (long passwords wrap).
    for start in (44, 40, 36, 32, 28, 24, 20):
        ssid_lines, ssid_size = _fit_lines(
            probe, guest.ssid, inner_w, start * SS, min(start, 22) * SS, stroke
        )
        pw_lines, pw_size = _fit_lines(
            probe, guest.password, inner_w, start * SS, min(start, 22) * SS, stroke
        )
        content_h = (
            block_h(ssid_lines, ssid_size)
            + block_h(pw_lines, pw_size)
            + 2 * block_gap  # between blocks, and before the divider
            + len(caption_lines) * int(caption_size * 1.4)
        )
        if content_h <= body_h - 2 * panel_pad:
            break

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    radius = RADIUS * SS

    # Card and header band (rounded top, square bottom edge).
    draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=CARD_BG)
    draw.rounded_rectangle((0, 0, w - 1, header_h), radius=radius, fill=ACCENT)
    draw.rectangle((0, header_h - radius, w - 1, header_h), fill=ACCENT)

    # Header: centered title + subtitle.
    title_size = 35 * SS
    sub_size = 18 * SS
    line_gap = 7 * SS
    ty = (header_h - (title_size + line_gap + sub_size)) // 2
    tw = _text_w(draw, title, title_size, SS)
    draw.text(((w - tw) // 2, ty), title, font=_font(title_size), fill=WHITE,
              stroke_width=SS, stroke_fill=WHITE)
    sub = "5 GHz Wi-Fi" + (" · Hidden network" if guest.hidden else "")
    sw = _text_w(draw, sub, sub_size)
    draw.text(((w - sw) // 2, ty + title_size + line_gap), sub, font=_font(sub_size),
              fill=(219, 234, 254))

    # Info panel on the right, full body height.
    draw.rounded_rectangle(
        (panel_x, body_top, panel_x + panel_w, body_top + body_h),
        radius=22 * SS,
        fill=PANEL_BG,
    )
    x = panel_x + panel_pad
    y = body_top + (body_h - content_h) // 2  # vertically centered
    for i, (label, lines, size) in enumerate(
        (("NETWORK", ssid_lines, ssid_size), ("PASSWORD", pw_lines, pw_size))
    ):
        if i:
            y += block_gap
        draw.text((x, y), label, font=_font(label_size), fill=ACCENT_DARK, stroke_width=1,
                  stroke_fill=ACCENT_DARK)
        y += label_size + label_gap
        for line in lines:
            draw.text((x, y), line, font=_font(size), fill=TEXT, stroke_width=stroke // 2,
                      stroke_fill=TEXT)
            y += int(size * 1.22)

    # Divider + caption.
    y += block_gap // 2
    draw.line((x, y, x + inner_w, y), fill=DIVIDER, width=2 * SS)
    y += block_gap // 2
    for line in caption_lines:
        draw.text((x, y), line, font=_font(caption_size), fill=MUTED)
        y += int(caption_size * 1.4)

    # Downscale for anti-aliasing, then paste the QR at native resolution,
    # vertically centered in the body.
    card = img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
    qr_y = HEADER_H + PAD + (BODY_H - qr.height) // 2
    card.paste(qr, (PAD, qr_y))

    out = io.BytesIO()
    card.save(out, format="PNG", optimize=True)
    return out.getvalue()
