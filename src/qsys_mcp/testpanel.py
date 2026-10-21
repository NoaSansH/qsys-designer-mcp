"""A test UCI generated from a design: snapshot buttons, a meter for every level, a fader and a mute
for every gain, and the indicators of Text Controllers. Built from read_design and the controls of
each block (helper command "controls"), so it works on any design."""

from __future__ import annotations

import re

BG, TXT, DIM = "#FF1C1D21", "#FFFFFFFF", "#FFB0B4BA"
BLUE, RED, GREEN, FADER = "#FF1565C0", "#FFC62828", "#FF2E7D32", "#FFD6D9DE"
LEVEL_RE = re.compile(r"^(meter_\d+|channel_\d+_peak_level|channel_\d+_digital_(input|output)_level)$")
SKIP_TC = re.compile(r"^(script_|code$|magic_number$|log_|reload$|script_blockly_code$)")


def _levels(controls: list[dict], limit: int = 8) -> list[str]:
    return [c["id"] for c in controls if LEVEL_RE.match(c["id"])][:limit]


NAV = 220   # with several pages, Designer shows a page list about this wide on the left


def build(design: dict, controls: dict, name: str = "Test", width: int = 1280, height: int = 800) -> dict:
    """UCI spec (for spec.ucis or edit_design's add_uci) with one page per section that has content."""
    comps = {c["name"]: c for c in design["components"]}
    full = width
    width = width - NAV   # laid out as if the page list were there, shifted at the end if it is
    pages = []

    def page(title):
        p = {"name": title, "color": BG, "elements": [{"type": "text", "text": title.upper(), "x": 20, "y": 12, "w": 900, "h": 30,
                                                       "color": TXT, "font_size": 20, "align": "Left"}]}
        pages.append(p)
        return p["elements"]

    # snapshot controllers: one row of Load buttons per bank
    banks = [b for b in design.get("snapshot_banks", []) if b.get("controller") in comps]
    if banks:
        els, y = page("Snapshots"), 60
        for b in banks:
            els.append({"type": "text", "text": b["name"], "x": 20, "y": y, "w": 600, "h": 22, "color": DIM, "font_size": 13, "align": "Left"})
            for i in range(1, b["count"] + 1):
                x = 20 + ((i - 1) % 8) * 150
                els.append({"type": "control", "component": b["controller"], "control": f"load_{i}", "x": x,
                            "y": y + 26 + ((i - 1) // 8) * 52, "w": 140, "h": 44, "label": f"{i}", "color": BLUE,
                            "off_color": "#FF3A3B40", "text_color": TXT, "font_size": 14, "corner_radius": 6})
            y += 26 + ((b["count"] - 1) // 8 + 1) * 52 + 16

    # levels: a column of meters per block that has any
    metered = [(n, _levels(controls[n]["controls"])) for n in comps if n in controls]
    metered = [(n, ids) for n, ids in metered if ids]
    if metered:
        els, x, y = page("Niveaux"), 20, 60
        for n, ids in metered:
            w = max(110, 34 * len(ids) + 10)
            if x + w > width - 20:
                x, y = 20, y + 330
            els.append({"type": "text", "text": n, "x": x, "y": y, "w": w, "h": 20, "color": TXT, "font_size": 11, "align": "Left"})
            for i, cid in enumerate(ids):
                els.append({"type": "control", "component": n, "control": cid, "x": x + 6 + 34 * i, "y": y + 26, "w": 26,
                            "h": 270, "style": "Meter"})
            x += w + 16

    # gains: a fader and a mute for every gain block
    gains = [n for n, c in comps.items() if c["class"] == "gain" and n in controls]
    if gains:
        els, x, y = page("Volumes"), 20, 60
        for n in gains:
            ids = {c["id"] for c in controls[n]["controls"]}
            if x + 130 > width - 20:
                x, y = 20, y + 360
            els.append({"type": "text", "text": n, "x": x, "y": y, "w": 120, "h": 20, "color": TXT, "font_size": 11, "align": "Left"})
            if "gain" in ids:
                els.append({"type": "control", "component": n, "control": "gain", "x": x + 20, "y": y + 26, "w": 70, "h": 250,
                            "style": "Fader", "color": FADER})
            if "mute" in ids:
                els.append({"type": "control", "component": n, "control": "mute", "x": x, "y": y + 286, "w": 110, "h": 40,
                            "label": "Mute", "color": RED, "off_color": "#FF3A3B40", "text_color": TXT, "font_size": 12,
                            "corner_radius": 6})
            x += 130

    # Text Controllers: their own controls (indicators read as LEDs or values)
    tcs = [n for n, c in comps.items() if c["class"] == "device_controller_script" and n in controls]
    rows = [(n, c) for n in tcs for c in controls[n]["controls"] if not SKIP_TC.match(c["id"])]
    if rows:
        els, y = page("États"), 60
        for n in tcs:
            mine = [c for m, c in rows if m == n]
            if not mine:
                continue
            els.append({"type": "text", "text": n, "x": 20, "y": y, "w": 600, "h": 22, "color": DIM, "font_size": 13, "align": "Left"})
            y += 28
            for i, c in enumerate(mine):
                x, yy = 20 + (i % 4) * min(300, (width - 40) // 4), y + (i // 4) * 44
                led = c.get("type") == "Boolean"
                els.append({"type": "control", "component": n, "control": c["id"], "x": x, "y": yy, "w": 24 if led else 100,
                            "h": 24 if led else 30, "style": "Led" if led else "Text", **({"color": GREEN} if led else {"font_size": 12})})
                els.append({"type": "text", "text": c["id"], "x": x + (32 if led else 108), "y": yy, "w": 180, "h": 26,
                            "color": TXT, "font_size": 12, "align": "Left"})
            y += ((len(mine) - 1) // 4 + 1) * 44 + 20
    if len(pages) > 1:
        for p in pages:
            for e in p["elements"]:
                e["x"] += NAV
    nav = {"navigation": "Left", "navigation_size": NAV} if len(pages) > 1 else {"navigation": "None"}
    return {"name": name, "width": full, "height": height, **nav, "pages": pages}
