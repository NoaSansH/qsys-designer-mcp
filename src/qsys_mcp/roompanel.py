"""A user UCI generated from a design: what a room's users touch, not a test page.

Home: the room modes (a snapshot bank's Load buttons), the mic mute and the room volume. Audio: every
room volume with its mute, the mic mute. Camera: the camera presets (a second bank) and the privacy
button. Pages without content are left out; several pages get Q-SYS's page tabs on the left.

What is what is found from the design (a bank whose controller isn't a camera's for the modes, gains
named or labelled volume/vol for volumes, mic/micro for the mic mute, a camera bank and an ONVIF
camera's toggle_privacy), or given in roles:
  {"modes": {"controller": "Snapshots_Salle", "labels": ["Presentation", "Video call", ...]},
   "volumes": ["Salle_Volume"], "mic_mute": "Micro_Mute",
   "camera": {"controller": "Snapshots_Camera", "labels": ["Wide", "Lectern"]},
   "privacy": {"component": "Camera_Salle", "control": "toggle_privacy"}}
"""

from __future__ import annotations

import re

BG, PANEL, TXT, DIM = "#FF16181C", "#FF22252B", "#FFFFFFFF", "#FF9AA0A6"
ON, OFF, MUTE, FADER = "#FF1E88E5", "#FF2E3238", "#FFE53935", "#FFD6D9DE"
NAV = 200
WORDS = {"fr": {"home": "Accueil", "audio": "Audio", "camera": "Caméra", "modes": "MODES", "volume": "VOLUME",
                "mic": "Micro coupé", "privacy": "Confidentialité", "mode": "Mode", "preset": "Position", "mute": "Muet"},
         "en": {"home": "Home", "audio": "Audio", "camera": "Camera", "modes": "MODES", "volume": "VOLUME",
                "mic": "Mic mute", "privacy": "Privacy", "mode": "Mode", "preset": "Preset", "mute": "Mute"}}
VOL_RE = re.compile(r"vol", re.I)
MIC_RE = re.compile(r"\bmic|micro", re.I)
CAM_RE = re.compile(r"cam", re.I)


def _label(c: dict) -> str:
    return f"{c['name']} {c.get('user_label') or ''}"


def roles(design: dict, given: dict | None = None) -> dict:
    """The roles used: those given, the rest found in the design."""
    given = dict(given or {})
    comps = design["components"]
    banks = [b for b in design.get("snapshot_banks", []) if b.get("controller")]
    for key, is_cam in (("modes", False), ("camera", True)):   # labels alone keep the bank found here
        if "controller" not in given.get(key, {}):
            b = next((b for b in banks if bool(CAM_RE.search(b["name"] + b["controller"])) == is_cam), None)
            if b:
                given[key] = {**given.get(key, {}), "controller": b["controller"]}
    if "volumes" not in given:
        given["volumes"] = [c["name"] for c in comps if c["class"] == "gain" and VOL_RE.search(_label(c))][:4]
    if "mic_mute" not in given:
        mic = next((c["name"] for c in comps if c["class"] == "gain" and MIC_RE.search(_label(c))), None)
        if mic:
            given["mic_mute"] = mic
    if "privacy" not in given:
        cam = next((c["name"] for c in comps if c["class"].startswith("onvif_camera")), None)
        if cam:
            given["privacy"] = {"component": cam, "control": "toggle_privacy"}
    return given


def _bank_numbers(design: dict, controller: str) -> list[int]:
    b = next((b for b in design.get("snapshot_banks", []) if b.get("controller") == controller), None)
    if b is None:
        return []
    return b.get("saved_snapshots") or list(range(1, b["count"] + 1))


def build(design: dict, name: str = "Salle", width: int = 1280, height: int = 800, given: dict | None = None,
          logo: str | None = None, lang: str = "fr") -> dict:
    """UCI spec (for spec.ucis or edit_design's add_uci)."""
    w = WORDS.get(lang, WORDS["en"])
    r = roles(design, given)
    comps = {c["name"] for c in design["components"]}
    pages = []

    def title(text, x, y, width_):
        return {"type": "text", "text": text, "x": x, "y": y, "w": width_, "h": 28, "color": DIM, "font_size": 16, "align": "Left"}

    def button(comp, control, label, x, y, bw=220, bh=90, on=ON, size=20):
        return {"type": "control", "component": comp, "control": control, "x": x, "y": y, "w": bw, "h": bh, "label": label,
                "color": on, "off_color": OFF, "text_color": TXT, "font_size": size, "corner_radius": 12}

    def presets(role, prefix, x0, y0, room, bh):
        """Buttons in up to 3 columns that fit in `room` pixels."""
        els = []
        cols = 3 if (room - 40) / 3 >= 180 else 2
        bw = min(240, int((room - 20 * (cols - 1)) / cols))
        numbers = _bank_numbers(design, role["controller"])
        labels = role.get("labels") or []
        for k, n in enumerate(numbers):
            label = labels[n - 1] if n - 1 < len(labels) else f"{prefix} {n}"
            els.append(button(role["controller"], f"load_{n}", label, x0 + (k % cols) * (bw + 20), y0 + (k // cols) * (bh + 20), bw, bh))
        return els

    def volume(comp, x, y, h):
        return [{"type": "control", "component": comp, "control": "gain", "style": "Fader", "x": x, "y": y, "w": 90, "h": h,
                 "color": FADER},
                button(comp, "mute", w["mute"], x - 5, y + h + 16, 100, 60, MUTE, 16)]

    x0 = 40
    modes = r.get("modes") if r.get("modes", {}).get("controller") in comps else None
    vols = [v for v in r.get("volumes", []) if v in comps]
    cam = r.get("camera") if r.get("camera", {}).get("controller") in comps else None
    priv = r.get("privacy") if (r.get("privacy") or {}).get("component") in comps else None
    nav = bool(cam or priv or len(vols) > 1) and bool(modes or vols or r.get("mic_mute") in comps)
    cw = width - NAV if nav else width            # laid out beside the page tabs
    home = []
    if modes:
        home.append(title(w["modes"], x0, 40, min(600, cw - 360 - 30 - x0)))
        home += presets(modes, w["mode"], x0, 80, (cw - 360 - 30 - x0) if vols or r.get("mic_mute") in comps else cw - 2 * x0, 110)
    side = cw - 360
    if vols:
        home.append(title(w["volume"], side, 40, 300))
        home += volume(vols[0], side + 30, 80, height - 300)
    if r.get("mic_mute") in comps:
        home.append(button(r["mic_mute"], "mute", w["mic"], side + 150, 80, 160, 90, MUTE, 18))
    if home:
        pages.append({"name": w["home"], "color": BG, "elements": home})

    audio = []
    for k, v in enumerate(vols):
        label = next((c.get("user_label") or c["name"] for c in design["components"] if c["name"] == v), v)
        audio.append(title(label.replace("\n", " "), x0 + k * 220, 40, 200))
        audio += volume(v, x0 + 40 + k * 220, 80, height - 300)
    if r.get("mic_mute") in comps:
        audio.append(button(r["mic_mute"], "mute", w["mic"], x0 + max(len(vols), 1) * 220 + 40, 80, 200, 100, MUTE, 20))
    if len(vols) > 1:
        pages.append({"name": w["audio"], "color": BG, "elements": audio})

    camera = []
    if cam:
        camera.append(title(w["camera"].upper(), x0, 40, min(600, cw - 300 - 30 - x0)))
        camera += presets(cam, w["preset"], x0, 80, (cw - 300 - 30 - x0) if priv else cw - 2 * x0, 110)
    if priv:
        camera.append(button(priv["component"], priv["control"], w["privacy"], cw - 300, 80, 240, 110, MUTE, 20))
    if camera:
        pages.append({"name": w["camera"], "color": BG, "elements": camera})

    if not pages:
        raise ValueError("nothing for users found in this design (no snapshot bank with a controller, no volume, "
                         "no mic mute, no camera): give roles")
    nav = len(pages) > 1
    for p in pages:          # room for the page tabs, then the logo above everything
        for e in p["elements"]:
            if nav:
                e["x"] += NAV
        if logo:
            p["elements"].append({"type": "image", "file": logo, "x": width - 220, "y": height - 90, "w": 200, "h": 70})
    uci = {"name": name, "width": width, "height": height, "pages": pages}
    uci.update({"navigation": "Left", "navigation_size": NAV, "tab_style": "Classic"} if nav else {"navigation": "None"})
    return uci
