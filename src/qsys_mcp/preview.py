"""Pictures of a schematic page or a UCI page drawn from the saved design, without Designer.

They are drawn from what read_design returns: blocks with their size, labels, pins and signal names,
wires, group boxes and notes; UCI elements with their place, kind and label. Pins are spread evenly
along a block's sides, so the picture is close to Designer's but not identical: it is for checking a
layout (overlaps, reading order, notes, signal names) without opening Designer.
"""

from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFont

FONTS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
MAX_SIDE = 2400
BACKGROUND = (32, 36, 42)
BLOCK = {"io": (112, 98, 18), "control": (96, 40, 30), "dsp": (24, 44, 72)}
IO_HINTS = ("io_", "usb", "dante", "aes67", "camera", "bridge", "flex", "gpio", "serial", "hid", "status")
CONTROL_CLASSES = ("snapshot_controller", "device_controller_script", "custom_controls", "control_script")


def _font(size: float, bold: bool = False):
    for name in (("segoeuib.ttf",) if bold else ()) + ("segoeui.ttf", "Roboto-Regular.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(os.path.join(FONTS, name), max(6, round(size)))
        except OSError:
            continue
    return ImageFont.load_default()


def _rgba(color: str | None, default=(0, 0, 0, 0)) -> tuple:
    """'#AARRGGBB' or '#RRGGBB' -> (r, g, b, a)."""
    c = (color or "").lstrip("#")
    try:
        if len(c) == 8:
            return int(c[2:4], 16), int(c[4:6], 16), int(c[6:8], 16), int(c[0:2], 16)
        if len(c) == 6:
            return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), 255
    except ValueError:
        pass
    return default


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> list[str]:
    lines = []
    for para in (text or "").split("\n"):
        line = ""
        for word in para.split(" "):
            cand = f"{line} {word}".strip()
            if line and draw.textlength(cand, font=font) > width:
                lines.append(line)
                line = word
            else:
                line = cand
        lines.append(line)
    return lines


def _text(draw, box, text, font, fill, align="center", valign="center"):
    x0, y0, x1, y1 = box
    lines = _wrap(draw, text, font, max(10, x1 - x0 - 4))
    lh = font.size * 1.2
    y = {"top": y0 + 2, "center": (y0 + y1 - lh * len(lines)) / 2}.get(valign, y0 + 2)
    for ln in lines:
        w = draw.textlength(ln, font=font)
        x = {"left": x0 + 4, "center": (x0 + x1 - w) / 2, "right": x1 - w - 4}.get(align.lower(), (x0 + x1 - w) / 2)
        draw.text((x, y), ln, font=font, fill=fill)
        y += lh


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _kind(c: dict) -> str:
    cls = c["class"].lower()
    if cls in CONTROL_CLASSES or "plugin" in cls:
        return "control"
    return "io" if any(h in cls for h in IO_HINTS) else "dsp"


def schematic(design: dict, page: str | None = None) -> bytes:
    """PNG of one schematic page (default: the first)."""
    page = page or design["pages"][0]
    if page not in design["pages"]:
        raise ValueError(f"no page {page!r}; pages: {design['pages']}")
    comps = [dict(c) for c in design["components"] if c.get("page") == page]
    for c in comps:   # room for every pin row, as Designer draws them
        rows = max([sum(p["dir"] == d for p in c.get("pins", [])) for d in ("in", "out")] + [0])
        c["h"] = max(c.get("h") or 40, 14 + 10.5 * rows)
    graphics = [g for g in design.get("graphics", []) if g.get("page") == page]
    boxes = [(c["x"], c["y"], c["x"] + c.get("w", 80), c["y"] + c.get("h", 40)) for c in comps] + \
            [(g["x"], g["y"], g["x"] + g["w"], g["y"] + g["h"]) for g in graphics]
    if not boxes:
        raise ValueError(f"page {page!r} is empty")
    left, top = min(b[0] for b in boxes) - 30, min(b[1] for b in boxes) - 30
    right, bottom = max(b[2] for b in boxes) + 90, max(b[3] for b in boxes) + 30
    s = min(2.0, MAX_SIDE / max(right - left, bottom - top))

    def P(x, y):
        return (x - left) * s, (y - top) * s

    img = Image.new("RGBA", (round((right - left) * s), round((bottom - top) * s)), BACKGROUND + (255,))
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    for g in sorted((g for g in graphics if g["type"] == "group"), key=lambda g: -g["w"] * g["h"]):
        x0, y0 = P(g["x"], g["y"])
        x1, y1 = P(g["x"] + g["w"], g["y"] + g["h"])
        od.rounded_rectangle((x0, y0, x1, y1), radius=8 * s, fill=_rgba(g.get("fill"), (255, 255, 255, 20)),
                             outline=(200, 200, 200, 160), width=max(1, round(s)))
    img = Image.alpha_composite(img, overlay)
    d = ImageDraw.Draw(img)
    for g in graphics:
        x0, y0 = P(g["x"], g["y"])
        x1, y1 = P(g["x"] + g["w"], g["y"] + g["h"])
        if g["type"] == "group":
            d.text((x0 + 8 * s, y0 + 3 * s), g.get("text", ""), font=_font(13 * s, True), fill=(235, 235, 235))
        else:
            fill = _rgba(g.get("fill"))
            if fill[3]:
                d.rectangle((x0, y0, x1, y1), fill=fill[:3])
            color = _rgba(g.get("text_color"), (220, 220, 220, 255))
            if sum(color[:3]) < 200 and (fill[3] < 100 or sum(fill[:3]) < 300):   # dark text straight on the dark page
                color = (215, 215, 215, 255)
            _text(d, (x0, y0, x1, y1), g.get("text", ""), _font((g.get("font_size") or 10) * s, g.get("bold")), color[:3], "left")

    pins = {}
    for c in comps:
        x0, y0 = P(c["x"], c["y"])
        x1, y1 = P(c["x"] + c.get("w", 80), c["y"] + c.get("h", 40))
        d.rounded_rectangle((x0, y0, x1, y1), radius=3 * s, fill=BLOCK[_kind(c)], outline=(230, 230, 230), width=max(1, round(s)))
        _text(d, (x0, y0, x1, y1), c.get("user_label") or c["name"], _font(10 * s, True), (240, 240, 240))
        for side in ("in", "out"):
            ps = [p for p in c.get("pins", []) if p["dir"] == side]
            for i, p in enumerate(ps):
                py = y0 + (y1 - y0) * (i + 0.5) / len(ps)
                px = x0 if side == "in" else x1
                pins[f"{c['name']}.{side}.{p['index']}"] = (px, py)
                r = 2.5 * s
                d.ellipse((px - r, py - r, px + r, py + r), outline=(230, 230, 230), fill=BACKGROUND)
                if p.get("signal"):
                    f = _font(8 * s)
                    w = d.textlength(p["signal"], font=f)
                    tx = px + 5 * s if side == "out" else px - 5 * s - w
                    d.rectangle((tx - 2, py - f.size * 0.6, tx + w + 2, py + f.size * 0.6), fill=(250, 250, 250))
                    d.text((tx, py - f.size * 0.65), p["signal"], font=f, fill=(20, 20, 20))
    by_label = {tuple(x[:2]) for x in design.get("signal_links", [])}   # joined by name only: no wire drawn
    for w in design.get("wires", []):
        if tuple(w[:2]) in by_label:
            continue
        a, b = pins.get(w[0]), pins.get(w[1])
        if a and b:
            mid = (a[0] + b[0]) / 2
            d.line([a, (mid, a[1]), (mid, b[1]), b], fill=(225, 225, 225), width=max(1, round(s)))
    return _png(img)


CONTROL_DRAW = {"button": "button", "toggle": "button", "trigger": "button", "momentary": "button",
                "fader": "fader", "meter": "meter", "knob": "knob", "led": "led", "indicator": "led"}


def uci(design: dict, name: str | None = None, page: str | None = None) -> bytes:
    """PNG of one UCI page (default: the first UCI, its first page)."""
    ucis = design.get("ucis", [])
    if not ucis:
        raise ValueError("this design has no UCI")
    u = next((x for x in ucis if x["name"] == name), None) if name else ucis[0]
    if u is None:
        raise ValueError(f"no UCI {name!r}; UCIs: {[x['name'] for x in ucis]}")
    pg = next((p for p in u["pages"] if p["name"] == page), None) if page else u["pages"][0]
    if pg is None:
        raise ValueError(f"no page {page!r} in {u['name']}; pages: {[p['name'] for p in u['pages']]}")
    W, H = int(u.get("width") or 1280), int(u.get("height") or 800)
    for e in pg["elements"]:
        W, H = max(W, int(e["x"] + e["w"])), max(H, int(e["y"] + e["h"]))
    s = min(1.5, MAX_SIDE / max(W, H))
    img = Image.new("RGBA", (round(W * s), round(H * s)), (44, 48, 54, 255))
    d = ImageDraw.Draw(img)
    for e in sorted(pg["elements"], key=lambda e: e.get("z", 0)):
        x0, y0, x1, y1 = e["x"] * s, e["y"] * s, (e["x"] + e["w"]) * s, (e["y"] + e["h"]) * s
        label = e.get("label") or ""
        t = e.get("type", "")
        if t == "GroupBox":
            fill = _rgba(e.get("fill"), (255, 255, 255, 12))
            d.rounded_rectangle((x0, y0, x1, y1), radius=(e.get("corner_radius") or 6) * s,
                                fill=fill[:3] if fill[3] > 100 else None, outline=_rgba(e.get("stroke"), (150, 150, 150, 255))[:3])
            d.text((x0 + 8 * s, y0 + 3 * s), label, font=_font((e.get("font_size") or 12) * s, True), fill=(230, 230, 230))
        elif t in ("TextBlock", "Header"):
            _text(d, (x0, y0, x1, y1), label, _font((e.get("font_size") or (16 if t == "Header" else 12)) * s, t == "Header" or e.get("bold")),
                  _rgba(e.get("color"), (230, 230, 230, 255))[:3], e.get("align", "left"))
        else:
            style = (e.get("style") or "").lower()
            kind = next((v for k, v in CONTROL_DRAW.items() if k in style), "box")
            if kind == "button":
                d.rounded_rectangle((x0, y0, x1, y1), radius=6 * s, fill=(70, 90, 120), outline=(160, 180, 210))
                _text(d, (x0, y0, x1, y1), label or e.get("control", ""), _font(11 * s, True), (240, 240, 240))
            elif kind in ("fader", "meter"):
                d.rectangle((x0, y0, x1, y1), fill=(30, 30, 30), outline=(120, 120, 120))
                if kind == "meter":
                    d.rectangle((x0 + 2, y0 + (y1 - y0) * 0.4, x1 - 2, y1 - 2), fill=(60, 170, 80))
                else:
                    cy = (y0 + y1) / 2
                    d.rectangle((x0 + 2, cy - 4 * s, x1 - 2, cy + 4 * s), fill=(200, 200, 200))
            elif kind in ("knob", "led"):
                r = min(x1 - x0, y1 - y0) / 2
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(200, 60, 50) if kind == "led" else (90, 90, 90), outline=(200, 200, 200))
            else:
                d.rectangle((x0, y0, x1, y1), fill=(25, 25, 25), outline=(130, 130, 130))
                _text(d, (x0, y0, x1, y1), label or f"{e.get('component', '')}.{e.get('control', '')}", _font(10 * s), (220, 220, 220))
    return _png(img)
