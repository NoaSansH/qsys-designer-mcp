"""A saved design written back as a spec: build_design on it gives the same design again.

Any design can be taken up this way, including one made by hand in Designer: export it, change the spec
(add a room, rewire, rename), build it, and record its snapshots back with record_snapshots from the
contents exported alongside.

Properties are kept where they differ from the class's defaults; ids Designer makes up for each block
are left out. A plugin block carries the plugin's code as saved in the design, so the plugin doesn't need
to be installed to rebuild it. Text Controllers keep their configuration as it is.
"""

from __future__ import annotations

import re

from qsys_mcp import headless, snapshotdata

ALWAYS_SKIP = {"ScriptAccess", "unique_name", "code_name", "plugin_source", "config", "external_prefix",
               "external_prefix_mode", "internal_designator", "pae_assignment", "core_name", "network_id",
               "is_managed", "name", "location", "is_required", "extension_model", "page_index", "bridge_name",
               "interface_index"}
INVENTORY_NOISE = re.compile(r"^(Is|Max|Default|Copy|Status|USBBridge|ShouldBe|Peripheral|Model)|Available$|InUse$|Valid$|^CoreName$")
BRIDGE_KEYS = ("USBSlotType", "SpeakerphoneMode", "VideoBridge", "ZoomRoom", "BridgeName", "VideoFormat")
DESIGN_NOISE = re.compile(r"^(Is|Copy|Status)")


def _scalar(s):
    """A property as Designer's text gives it (culture formatted) -> a JSON value."""
    if not isinstance(s, str):
        return s
    if s in ("True", "False"):
        return s == "True"
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+[.,]\d+(E[-+]?\d+)?", s, re.I):
        return float(s.replace(",", "."))
    return s


def _origin(c: dict, core_name: str):
    """The Core block ('Flex Out') or the device block ({"item", "placeable"}) a component is."""
    inv = c.get("inventory")
    if not inv:
        return None
    return inv["placeable"] if inv.get("core") else {"item": inv["item"], "placeable": inv["placeable"]}


def _defaults(classes: set[str], inst) -> dict:
    plain = sorted(c for c in classes if not c.startswith("%PLUGIN%"))
    if not plain:
        return {}
    r = headless.run({"command": "probe", "classes": [{"class": c} for c in plain], "controls": False}, inst)
    return {x["class"]: x.get("properties", {}) for x in r["classes"] if not x.get("error")}


def _values(c: dict) -> dict:
    out = {}
    for k, v in c.get("values", {}).items():
        if k == "code" and c["class"] == "device_controller_script":
            continue
        if isinstance(v, dict) and v.get("value") is not None:
            out[k] = {"value": v["value"], "position": v["position"]} if v.get("position") is not None else v["value"]
        elif isinstance(v, dict) and v.get("string") is not None:
            out[k] = v["string"]
    return out


def _uci(u: dict) -> dict:
    out = {"name": u["name"]}
    for k in ("width", "height", "navigation", "tab_style", "navigation_size"):
        if u.get(k) not in (None, ""):
            out[k] = u[k]
    pages = []
    for p in u["pages"]:
        els = []
        for e in sorted(p["elements"], key=lambda e: e.get("z", 0)):
            box = {k: e[k] for k in ("x", "y", "w", "h") if k in e}
            t = e["type"]
            if t == "TextBlock":
                els.append({"type": "text", "text": e.get("label", ""), **box,
                            **{k: e[k] for k in ("color", "font_size", "align") if e.get(k) not in (None, "")}})
            elif t == "ControlStyle":
                el = {"type": "control", "component": e["component"], "control": e["control"], **box}
                for k in ("style", "label", "color", "text_color", "off_color", "font_size", "corner_radius", "show_textbox",
                          "image_on", "image_off", "image_pressed_on", "image_pressed_off"):
                    if e.get(k) not in (None, ""):
                        el[k] = e[k]
                els.append(el)
            elif t in ("GroupBox", "Header"):
                el = {"type": "group_box" if t == "GroupBox" else "header", **box}
                if e.get("label"):
                    el["text"] = e["label"]
                for k in ("font_size", "bold", "color", "align") + (("fill", "stroke", "stroke_width", "corner_radius") if t == "GroupBox" else ()):
                    if e.get(k) not in (None, ""):
                        el[k] = int(e[k]) if k == "stroke_width" else e[k]
                els.append(el)
            elif t == "GraphicImage" and e.get("data"):
                els.append({"type": "image", "data": e["data"], "format": "svg" if e.get("format") == "svg" else "png", **box})
            elif t == "Region" and e.get("page"):
                els.append({"type": "page_link", "page": e["page"], **box, **({"text": e["label"]} if e.get("label") else {})})
        pages.append({"name": p["name"], **({"color": p["color"]} if p.get("color") else {}), "elements": els})
    out["pages"] = pages
    return out


def export(path: str, design: dict | None = None) -> dict:
    """{"spec": a spec that builds this design, "snapshots": its snapshot contents for record_snapshots,
    "notes": what couldn't be carried over}."""
    from qsys_mcp import designs
    design = design or designs.read(path)
    inst = headless.pick(for_file=path)
    core = design["core"]
    notes = []
    spec: dict = {"core": {"model": core["model"].replace(" ", ""), "name": core["name"]}, "pages": design["pages"]}
    bridge = core.get("usb_bridge") or {}
    if bridge.get("USBSlotType") not in (None, "None"):
        spec["core"]["usb_bridge"] = {k: bridge[k] for k in BRIDGE_KEYS if k in bridge and k != "BridgeName"}
    elif bridge.get("USBCount") and any(s.get("type") == "Soundcard" for s in design.get("usb_slots", [])):
        spec["usb_sound_cards"] = bridge["USBCount"]                 # the Core's plain USB sound cards
    dp = {k: _scalar(v) for k, v in (design.get("design_properties") or {}).items() if not DESIGN_NOISE.match(k)}
    if dp:
        spec["design_properties"] = dp
    inventory = []
    for i in design["inventory"]:
        if i["type"] == "Core":
            continue
        e = {"type": i["type"], "name": i["name"], **({"model": i["model"]} if i.get("model") else {})}
        sets = {k: v for k, v in (i.get("set") or {}).items() if not INVENTORY_NOISE.match(k) and v is not None}
        if sets:
            e["set"] = sets
        b = i.get("usb_bridge") or {}
        if b.get("USBSlotType") not in (None, "None"):
            e["usb_bridge"] = {k: b[k] for k in BRIDGE_KEYS if k in b}
        inventory.append(e)
    if inventory:
        spec["inventory"] = inventory

    controllers = {b["controller"] for b in design.get("snapshot_banks", []) if b.get("controller")}
    by_name = {c["name"]: c for c in design["components"]}
    defaults = _defaults({c["class"] for c in design["components"] if _origin(c, core["name"]) is None}, inst)
    comps = []
    for c in design["components"]:
        if c["name"] in controllers:
            continue
        e = {"name": c["name"]}
        origin = _origin(c, core["name"])
        if origin is not None:
            e["inventory"] = origin
        else:
            e["class"] = c["class"]
            props = c.get("properties", {})
            base = defaults.get(c["class"], {})
            keep = {k: _scalar(v) for k, v in props.items() if k not in ALWAYS_SKIP and (k not in base or base[k] != v)}
            if keep:
                e["properties"] = keep
            if c["class"].startswith("%PLUGIN%"):
                e["plugin_source"] = props.get("plugin_source", "")
            if c["class"] == "device_controller_script":
                e["text_controller"] = {"config": props.get("config", ""), "lua": c.get("values", {}).get("code", {}).get("string", "")}
        e.update({"page": c["page"], "x": c["x"], "y": c["y"]})
        if c.get("user_label") and c["user_label"] != c["name"]:
            e["label"] = c["user_label"]
        if c.get("script_access", "None") != "None":
            e["script_access"] = c["script_access"]
        vals = _values(c)
        if vals:
            e["values"] = vals
        if c["class"] not in ("device_controller_script",) and not c["class"].startswith("%PLUGIN%"):
            pins = [p["code"] for p in c.get("pins", []) if p.get("control") and p.get("code")]
            if pins:
                e["control_pins"] = pins
        comps.append(e)
    spec["components"] = comps

    links = {tuple(x[:2]) for x in design.get("signal_links", [])}
    signal_of = {f"{c['name']}.{p['dir']}.{p['index']}": p.get("signal") for c in design["components"] for p in c.get("pins", [])}
    wires = []
    for a, b in design.get("wires", []):
        if (a, b) in links:
            continue
        wires.append([a, b, signal_of[a]] if signal_of.get(a) else [a, b])
    spec["wires"] = wires
    if design.get("signal_links"):
        spec["signals"] = [list(x) for x in design["signal_links"]]
    graphics = []
    for g in design.get("graphics", []):
        e = {k: g[k] for k in ("type", "page", "x", "y", "w", "h", "text") if k in g}
        for k in ("fill", "text_color", "font_size", "bold"):
            if g.get(k) not in (None, ""):
                e[k] = g[k]
        graphics.append(e)
    if graphics:
        spec["graphics"] = graphics

    banks = []
    for b in design.get("snapshot_banks", []):
        e = {"name": b["name"], "count": b["count"], "mode": b.get("mode", "Normal"),
             "controls": [({"component": m["component"], "all": True} if m.get("all") else
                           {"component": m["component"], "controls": m["controls"]}) for m in b["controls"]]}
        ctl = by_name.get(b.get("controller") or "")
        if ctl:
            e["controller"] = {"name": ctl["name"], "page": ctl["page"], "x": ctl["x"], "y": ctl["y"],
                               "script_access": ctl.get("script_access", "None"),
                               **({"label": ctl["user_label"]} if ctl.get("user_label") else {})}
        banks.append(e)
    if banks:
        spec["snapshot_banks"] = banks
    if design.get("named_controls"):
        spec["named_controls"] = [{k: n[k] for k in ("name", "component", "control") if k in n} for n in design["named_controls"]]
    if design.get("global_snapshot_count"):
        spec["global_snapshot_count"] = design["global_snapshot_count"]
    if design.get("ucis"):
        spec["ucis"] = [_uci(u) for u in design["ucis"]]

    snapshots = {}
    try:
        for bank, snaps in snapshotdata.contents(path, design).items():
            controller = next((b.get("controller") for b in design.get("snapshot_banks", []) if b["name"] == bank), None)
            if not controller:
                notes.append(f"bank {bank}: no Snapshot Controller, its snapshots can't be recorded back")
                continue
            snapshots[bank] = {"controller": controller, "snapshots": {
                n: [{"component": k.split(".", 1)[0], "control": k.split(".", 1)[1], "value": v} for k, v in vals.items()
                    if not k.startswith("?")] for n, vals in snaps.items()}}
    except snapshotdata.SnapshotFormatError as e:
        notes.append(f"snapshot contents not read: {e}")
    return {"spec": spec, "snapshots": snapshots, "notes": notes}
