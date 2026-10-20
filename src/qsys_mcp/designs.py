"""Design specs: validation, building, reading back and signal-path checks.

A spec is JSON (see qsys://spec-format). Building and reading go through the headless helper;
the checks here work on the read-back file, never on the spec, so they test what was saved.
"""

from __future__ import annotations

import copy
import os
import re
from collections import deque
from pathlib import Path

from qsys_mcp import headless

ACCESS = {"None", "External", "Script", "All"}
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")
ENDPOINT_RE = re.compile(r"^(?P<name>.+)\.(?P<dir>in|out)\.(?P<pin>.+)$")
COLOR_RE = re.compile(r"^#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})$")
UCI_PIXELS = range(100, 8193)
FRAME_ONLY = ("fill", "stroke", "stroke_width", "corner_radius")   # group_box keys a header doesn't have
# inputs that never pass signal to the block's outputs (the AEC subtracts its reference)
NON_PASSING = {("acoustic_echo_canceler_simd", "Reference"), ("acoustic_echo_canceler", "Reference")}
USB_TYPES = ("speakerphone", "soundcard", "none")
USB_KEYS = {"type", "count", "mode", "video_bridge", "set", "slots"}
CORE_KEYS = {"model", "name", "set", "usb"}
ITEM_KEYS = {"type", "name", "set", "usb"}
INVENTORY_REF_KEYS = {"item", "placeable", "class"}
OLD_HELPER = ("This Designer uses the prebuilt .NET helper, which is older than the hardware settings (core.set, "
              "core.usb, inventory usb, design). Rebuild it: dotnet publish helper-net8 -c Release -o src/qsys_mcp/helper/net8")
PIN_DIRS = ("in", "out", "both")
# a Text Controller control's "pins" setting, as a pin direction
PIN_STYLES = {"None": "none", "Input": "in", "Output": "out", "Both": "both"}
# what the helper reports for a bank whose snapshot values it could not store (the engine writes them)
SNAPSHOT_VALUES_PENDING = "snapshot values need save_snapshots"
# schematic graphics, and the keys each type doesn't have
GRAPHICS = {"text": set(), "header": {"fill", "stroke", "stroke_width", "corner_radius", "valign"}, "group_box": {"valign"}}
ALIGN = {"align": ("Left", "Center", "Right"), "valign": ("Top", "Center", "Bottom")}
COLOUR_RE = re.compile(r"#(?:[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})")


class SpecError(ValueError):
    pass


def _norm(s) -> str:
    """Lower case, letters and digits only ('Sound Card' -> 'soundcard'), as the helper compares names."""
    return re.sub(r"[^0-9a-z]", "", str(s).lower())


def _check_set(where: str, values, problems: list[str]) -> None:
    """A set block: {"Property": number, boolean, string or null}."""
    if not isinstance(values, dict):
        problems.append(f"{where} must be an object of property names and values")
        return
    for k, v in values.items():
        if isinstance(v, (dict, list)):
            problems.append(f"{where}.{k} must be a number, boolean or string")


def _check_usb(where: str, usb, problems: list[str]) -> None:
    """A usb block: {type, count, mode, video_bridge, set, slots}."""
    if not isinstance(usb, dict):
        problems.append(f"{where} must be an object")
        return
    for k in sorted(set(usb) - USB_KEYS):
        problems.append(f"{where}: unknown key {k!r} (use {', '.join(sorted(USB_KEYS))})")
    kind = usb.get("type")
    if kind is not None and _norm(kind) not in USB_TYPES:
        problems.append(f"{where}.type must be speakerphone, soundcard or none, not {kind!r}")
    count = usb.get("count")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 0):
        problems.append(f"{where}.count must be a whole number, 0 or more")
    elif count and kind is not None and _norm(kind) == "none":
        problems.append(f"{where}: type none can't have count {count}")
    if "mode" in usb and (isinstance(usb["mode"], bool) or not isinstance(usb["mode"], (str, int))):
        problems.append(f"{where}.mode must be a name (e.g. WithEchoCancel) or a number")
    if "video_bridge" in usb and not isinstance(usb["video_bridge"], bool):
        problems.append(f"{where}.video_bridge must be true or false")
    if "set" in usb:
        _check_set(f"{where}.set", usb["set"], problems)
    if "slots" in usb:
        if not isinstance(usb["slots"], list):
            problems.append(f"{where}.slots must be a list of objects, one per USB slot")
        else:
            for j, s in enumerate(usb["slots"]):
                _check_set(f"{where}.slots[{j}]", s, problems)


def _check_hardware(spec: dict, problems: list[str]) -> None:
    """core.set / core.usb, inventory items' set / usb, design.set."""
    core = spec.get("core")
    if core is not None and not isinstance(core, dict):
        problems.append("core must be an object")
    elif core:
        for k in sorted(set(core) - CORE_KEYS):
            problems.append(f"core: unknown key {k!r} (use {', '.join(sorted(CORE_KEYS))})")
        if "set" in core:
            _check_set("core.set", core["set"], problems)
        if "usb" in core:
            _check_usb("core.usb", core["usb"], problems)
            if spec.get("usb_sound_cards") is not None:
                problems.append("give the Core's USB once: core.usb or usb_sound_cards, not both")
    items = spec.get("inventory", [])
    if not isinstance(items, list):
        problems.append("inventory must be a list of items")
        items = []
    for i, inv in enumerate(items):
        where = f"inventory[{i}]"
        if not isinstance(inv, dict) or not isinstance(inv.get("type"), str) or not inv.get("type"):
            problems.append(f"{where}: needs a type (see designer_versions -> inventory_types)")
            continue
        for k in sorted(set(inv) - ITEM_KEYS):
            problems.append(f"{where}: unknown key {k!r} (use {', '.join(sorted(ITEM_KEYS))})")
        if "set" in inv:
            _check_set(f"{where}.set", inv["set"], problems)
        if "usb" in inv:
            _check_usb(f"{where}.usb", inv["usb"], problems)
    design = spec.get("design")
    if design is not None:
        if not isinstance(design, dict):
            problems.append('design must be an object like {"set": {...}}')
        else:
            for k in sorted(set(design) - {"set"}):
                problems.append(f"design: unknown key {k!r} (only 'set')")
            if "set" in design:
                _check_set("design.set", design["set"], problems)


def _check_inventory_ref(name: str, ref, problems: list[str]) -> None:
    """A component's inventory: "Placeable Name" or {"item", "placeable", "class"}."""
    if isinstance(ref, str):
        return
    if not isinstance(ref, dict):
        problems.append(f'{name}: inventory must be a placeable name or {{"item", "placeable", "class"}}')
        return
    for k in sorted(set(ref) - INVENTORY_REF_KEYS):
        problems.append(f"{name}: unknown inventory key {k!r} (use item, placeable, class)")
    for k in sorted(INVENTORY_REF_KEYS & set(ref)):
        if not isinstance(ref[k], str) or not ref[k]:
            problems.append(f"{name}: inventory.{k} must be a name")
    if not INVENTORY_REF_KEYS & set(ref):
        problems.append(f"{name}: inventory needs an item, a placeable or a class")


def uses_hardware(spec: dict) -> bool:
    """Does the spec use what older helpers lack (core.set, core.usb, inventory usb, design, blocks by class)?"""
    core = spec.get("core") if isinstance(spec.get("core"), dict) else {}
    refs = [c.get("inventory") for c in spec.get("components", []) if isinstance(c, dict)]
    return bool(core.get("set") or core.get("usb") or spec.get("design")
                or any(isinstance(i, dict) and i.get("usb") for i in spec.get("inventory", []))
                or any(isinstance(r, dict) and (r.get("class") or not r.get("placeable")) for r in refs))


_HARDWARE_OK: set = set()


def require_hardware(inst: headless.Install) -> None:
    """Designer 10+ runs the prebuilt helper: check it knows the hardware settings before relying on them."""
    if inst in _HARDWARE_OK or not headless.is_dotnet_core(inst):
        return
    if "hardware" not in headless.run({"command": "info"}, inst).get("features", []):
        raise headless.HelperError(OLD_HELPER)
    _HARDWARE_OK.add(inst)


def validate(spec: dict, base_dir: str | Path | None = None) -> tuple[dict, list[str]]:
    """Check a spec and return (prepared spec, problems). Inlines text_controller.lua_file."""
    spec = copy.deepcopy(spec)
    problems: list[str] = []
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    pages = spec.get("pages") or ["Main"]
    spec["pages"] = pages
    names: set[str] = set()
    pin_dirs: dict[str, dict[str, str | None]] = {}
    for i, c in enumerate(spec.get("components", [])):
        where = f"components[{i}]"
        name = c.get("name")
        if not name or not NAME_RE.match(name):
            problems.append(f"{where}: name {name!r} must start with a letter and use letters, digits, _ or -")
        elif name in names:
            problems.append(f"{where}: duplicate name {name}")
        names.add(name)
        if bool(c.get("class")) == bool(c.get("inventory")):
            problems.append(f"{name}: give exactly one of 'class' or 'inventory'")
        elif c.get("inventory"):
            _check_inventory_ref(name, c["inventory"], problems)
        if c.get("page", pages[0]) not in pages:
            problems.append(f"{name}: page {c.get('page')!r} is not in spec.pages")
        if c.get("script_access", "None") not in ACCESS:
            problems.append(f"{name}: script_access must be one of {sorted(ACCESS)}")
        tc = c.get("text_controller")
        if tc:
            if c.get("class") != "device_controller_script":
                problems.append(f"{name}: text_controller needs class device_controller_script")
            if "lua_file" in tc:
                p = Path(tc["lua_file"])
                if not p.is_absolute() and base_dir:
                    p = Path(base_dir) / p
                try:
                    tc["lua"] = p.read_text(encoding="utf-8")
                except OSError as e:
                    problems.append(f"{name}: cannot read lua_file {p}: {e}")
                del tc["lua_file"]
            if "lua" in tc:
                from qsys_mcp import lua
                chk = lua.check_syntax(tc["lua"])
                if not chk["ok"]:
                    problems.append(f"{name}: Lua syntax error: {chk.get('error')}")
        pin_dirs[name] = _control_pins(c, name, problems)
    _validate_wires(spec.get("wires", []), names, problems, pin_dirs)
    _validate_graphics(spec.get("graphics", []), pages, problems)
    names |= _validate_snapshots(spec, names, pages, problems)   # UCIs can use a bank controller's buttons
    for u in spec.get("ucis", []):
        problems += _uci_size_problems(u)
        for p in u.get("pages", []):
            for j, e in enumerate(p.get("elements", [])):
                where = f"UCI {u.get('name')}/{p.get('name')} element {j}"
                kind = e.get("type", "control")
                if kind == "control":
                    if e.get("component") not in names:
                        problems.append(f"{where}: no component {e.get('component')}")
                elif kind in ("group_box", "header"):
                    problems += _graphic_problems(where, kind, e)
                elif kind != "text":
                    problems.append(f"{where}: type must be control, text, group_box or header, not {kind!r}")
    _check_hardware(spec, problems)
    return spec, problems


def _control_pins(c: dict, name: str, problems: list[str]) -> dict[str, str | None]:
    """Check a component's control_pins and Text Controller pins. Returns each control's pin direction
    (lowercased name -> 'in', 'out', 'both', 'none', or None when Designer picks it) for the wire checks."""
    dirs: dict[str, str | None] = {}
    tc = c.get("text_controller") if isinstance(c.get("text_controller"), dict) else {}
    own = set()
    for ctl in tc.get("controls") or []:
        if not isinstance(ctl, dict) or not ctl.get("name"):
            continue
        own.add(ctl["name"].lower())
        style = ctl["pins"] if ctl.get("pins") is not None else "None"
        key = style.capitalize() if isinstance(style, str) else None
        if key not in PIN_STYLES:
            problems.append(f"{name}: Text Controller control {ctl['name']}: pins must be None, Input, Output or Both, not {style!r}")
        elif ctl.get("count", 1) == 1:   # an array's pins are named per element ("Name 1"...)
            dirs[ctl["name"].lower()] = PIN_STYLES[key]
    cps = c.get("control_pins")
    if cps is None:
        return dirs
    if not isinstance(cps, list):
        problems.append(f'{name}: control_pins must be a list of control names or {{"control": ..., "dir": ...}}')
        return dirs
    seen: set[str] = set()
    for j, e in enumerate(cps):
        where = f"{name}.control_pins[{j}]"
        if isinstance(e, str):
            ctl, d = e, None
        elif isinstance(e, dict):
            ctl, d = e.get("control"), e.get("dir")
            extra = sorted(set(e) - {"control", "dir"})
            if extra:
                problems.append(f"{where}: unknown key(s) {', '.join(extra)}; use control and dir")
        else:
            problems.append(f'{where}: give a control code name or {{"control": ..., "dir": ...}}')
            continue
        if not isinstance(ctl, str) or not ctl:
            problems.append(f"{where}: no control code name")
            continue
        if d is not None and d not in PIN_DIRS:
            problems.append(f"{where}: dir must be in, out or both, not {d!r}")
            d = None
        if ctl in seen:
            problems.append(f"{name}: control {ctl} is listed twice in control_pins")
        elif ctl.lower() in own:
            problems.append(f'{name}: {ctl} is one of the Text Controller\'s own controls: give it "pins" instead of a control_pins entry')
        else:
            dirs[ctl.lower()] = d
        seen.add(ctl)
    return dirs


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _uci_size_problems(u: dict) -> list[str]:
    where, size = f"UCI {u.get('name')}", {k: u[k] for k in ("width", "height") if k in u}
    if len(size) == 1:
        return [f"{where}: give both width and height, or neither"]
    return [f"{where}: {k} must be a whole number of pixels from 100 to 8192, not {v!r}"
            for k, v in size.items() if not _is_int(v) or v not in UCI_PIXELS]


def _graphic_problems(where: str, kind: str, e: dict) -> list[str]:
    """group_box and header keys: x, y, w, h, text, font_size, bold, color, align (+ the FRAME_ONLY ones)."""
    problems = []
    extra = [k for k in FRAME_ONLY if k in e] if kind == "header" else []
    if extra:
        problems.append(f"{where}: a header has no {', '.join(extra)} (a group_box does)")
    for k in ("color", "fill", "stroke"):
        if k in e and k not in extra and not (isinstance(e[k], str) and COLOR_RE.match(e[k])):
            problems.append(f"{where}: {k} must be a colour like #AARRGGBB, not {e[k]!r}")
    for k in ("x", "y", "w", "h", "font_size", "corner_radius"):
        if k in e and k not in extra:
            v = e[k]
            if not _is_number(v) or (k in ("w", "h", "font_size") and v <= 0) or (k == "corner_radius" and v < 0):
                rule = "a number" if k in ("x", "y") else "zero or more" if k == "corner_radius" else "more than zero"
                problems.append(f"{where}: {k} must be {rule}, not {v!r}")
    if "stroke_width" in e and "stroke_width" not in extra and not (_is_int(e["stroke_width"]) and e["stroke_width"] >= 0):
        problems.append(f"{where}: stroke_width must be a whole number of pixels, not {e['stroke_width']!r}")
    if "bold" in e and not isinstance(e["bold"], bool):
        problems.append(f"{where}: bold must be true or false, not {e['bold']!r}")
    if "align" in e and str(e["align"]).lower() not in ("left", "center", "right"):
        problems.append(f"{where}: align must be Left, Center or Right, not {e['align']!r}")
    if "text" in e and not isinstance(e["text"], str):
        problems.append(f"{where}: text must be a string, not {e['text']!r}")
    return problems


def _validate_wires(wires: list, names: set[str], problems: list[str], pin_dirs: dict | None = None) -> None:
    """Wires are [from, to], or [from, to, signal] / {"from", "to", "signal"} for a Signal Name.
    A name has one source, an output carries one name, and an input takes one wire or one name."""
    fed: dict[tuple, tuple] = {}      # input pin -> (signal or None, wire index)
    carries: dict[tuple, tuple] = {}  # output pin -> (signal, wire index)
    sources: dict[str, tuple] = {}    # signal, case-folded -> (output pin, endpoint, signal, wire index)
    for i, w in enumerate(wires):
        if isinstance(w, dict):
            ends, named, signal = [w.get("from"), w.get("to")], "signal" in w, w.get("signal")
        elif isinstance(w, list) and len(w) in (2, 3):
            ends, named, signal = w[:2], len(w) == 3, w[2] if len(w) == 3 else None
        else:
            problems.append(f"wires[{i}]: expected [from, to] or [from, to, signal]")
            continue
        ok = True
        if named and not (isinstance(signal, str) and signal.strip()):
            problems.append(f"wires[{i}]: the signal name must be a non-empty string, not {signal!r}")
            ok = False
        for end, d in zip(ends, ("out", "in")):
            m = ENDPOINT_RE.match(end if isinstance(end, str) else "")
            if not m or m["dir"] != d:
                problems.append(f"wires[{i}]: {end!r} should look like NAME.{d}.N or NAME.{d}.PinName")
                ok = False
            elif m["name"] not in names:
                problems.append(f"wires[{i}]: no component {m['name']}")
                ok = False
            else:
                have = (pin_dirs or {}).get(m["name"], {}).get(m["pin"].lower())
                if have == "none":
                    problems.append(f"wires[{i}]: {end!r}: the Text Controller control {m['pin']} has no pins (set its \"pins\")")
                elif have in ("in", "out") and have != d:
                    problems.append(f"wires[{i}]: {end!r}: {m['pin']} only has an {'input' if have == 'in' else 'output'} pin")
        if not ok:
            continue
        src, dst = _pin_key(ends[0]), _pin_key(ends[1])
        if not named:
            prev = fed.setdefault(dst, (None, i))
            if prev[0] is not None:
                problems.append(f"wires[{i}]: {ends[1]} already takes signal {prev[0]!r} (wires[{prev[1]}]); "
                                "an input takes one wire or one signal name")
            continue
        key = signal.casefold()
        prev = carries.setdefault(src, (signal, i))
        if prev[0].casefold() != key:
            problems.append(f"wires[{i}]: {ends[0]} already carries signal {prev[0]!r} (wires[{prev[1]}]); "
                            "an output carries one name")
        prev = sources.setdefault(key, (src, ends[0], signal, i))
        if _differ(prev[0], src):
            problems.append(f"wires[{i}]: signal {signal!r} already comes from {prev[1]} (wires[{prev[3]}]); a name has one source"
                            + ("" if prev[2] == signal else " (names are not case sensitive)"))
        prev = fed.setdefault(dst, (signal, i))
        if prev[0] is None:
            problems.append(f"wires[{i}]: {ends[1]} already has a wire (wires[{prev[1]}]); an input takes one wire or one signal name")
        elif prev[0].casefold() != key:
            problems.append(f"wires[{i}]: {ends[1]} already takes signal {prev[0]!r} (wires[{prev[1]}])")


def _validate_graphics(graphics: list, pages: list[str], problems: list[str]) -> None:
    """Schematic text, headers and group boxes: type, page, alignment, colours and numbers."""
    for i, g in enumerate(graphics):
        where = f"graphics[{i}]"
        if not isinstance(g, dict) or g.get("type") not in GRAPHICS:
            problems.append(f"{where}: type must be one of {', '.join(GRAPHICS)}")
            continue
        if g.get("page", pages[0]) not in pages:
            problems.append(f"{where}: page {g.get('page')!r} is not in spec.pages")
        for k in sorted(g.keys() & GRAPHICS[g["type"]]):
            problems.append(f"{where}: a {g['type']} has no {k}")
        for k, allowed in ALIGN.items():
            if k in g and str(g[k]).lower() not in {a.lower() for a in allowed}:
                problems.append(f"{where}: {k} must be one of {', '.join(allowed)}")
        for k in ("color", "fill", "stroke"):
            if k in g and not (isinstance(g[k], str) and COLOUR_RE.fullmatch(g[k])):
                problems.append(f"{where}: {k} {g[k]!r} should be a colour like #FF336699 (#AARRGGBB)")
        for k in ("x", "y", "w", "h", "font_size", "stroke_width", "corner_radius"):
            if k in g and (isinstance(g[k], bool) or not isinstance(g[k], (int, float))):
                problems.append(f"{where}: {k} must be a number")
        if "bold" in g and not isinstance(g["bold"], bool):
            problems.append(f"{where}: bold must be true or false")
        if "text" in g and not isinstance(g["text"], str):
            problems.append(f"{where}: text must be a string")


def _pin_key(end: str) -> tuple:
    """A wire endpoint as (component, dir, pin number or lower-case pin name)."""
    m = ENDPOINT_RE.match(end)
    return m["name"], m["dir"], int(m["pin"]) if m["pin"].strip().isdecimal() else m["pin"].lower()


def _differ(a: tuple, b: tuple) -> bool:
    """Surely different pins? A pin number and a pin name on the same block may be the same pin."""
    return a[:2] != b[:2] or (type(a[2]) is type(b[2]) and a[2] != b[2])


def build(spec: dict, path: str, overwrite: bool = False, designer_version: str | None = None,
          base_dir: str | None = None) -> dict:
    path = os.path.abspath(path)
    if not path.lower().endswith(".qsys"):
        raise SpecError("path must end in .qsys")
    if os.path.exists(path) and not overwrite:
        raise FileExistsError(f"{path} exists. Pick a new file name, or pass overwrite=True if replacing it is intended.")
    prepared, problems = validate(spec, base_dir)
    if problems:
        raise SpecError("spec problems:\n- " + "\n- ".join(problems))
    inst = headless.pick(designer_version)
    if uses_hardware(prepared):
        require_hardware(inst)
    res = headless.run({"command": "build", "path": path, "overwrite": overwrite, "spec": prepared,
                        "positions": headless.load_positions(inst)}, inst)
    sources: dict[str, int] = {}
    for v in res.get("values", []):
        sources[v["source"]] = sources.get(v["source"], 0) + 1
    res["value_sources"] = sources
    unconfirmed = [f"{v['component']}.{v['control']}={v['value']}" for v in res.get("values", [])
                   if v["source"].startswith("assumed")]
    res["unconfirmed_values"] = unconfirmed
    if unconfirmed:
        res["next_step"] = ("Some stored values use an assumed taper. Run calibrate_values on this file to measure "
                            "them on the engine (needs Designer's emulator), or check them at runtime over QRC.")
    if prepared.get("snapshots") and "snapshots" not in res:
        raise headless.HelperError(f"{path} was written without its snapshot banks: this Designer's helper predates them. "
                                   "Rebuild the .NET 8 helper (README, Development), then build again with overwrite=True.")
    pending = [b["name"] for b in (res.get("snapshots") or {}).get("banks", []) if b.get("values") == SNAPSHOT_VALUES_PENDING]
    if pending:
        hint = (f"Snapshot values of {', '.join(pending)} aren't stored yet (the engine writes them): run save_snapshots "
                "on this file with the same spec to store them through Designer's emulator.")
        res["next_step"] = f"{res['next_step']} {hint}" if "next_step" in res else hint
    res.pop("values", None) if len(res.get("values", [])) > 200 else None
    return res


def read(path: str, designer_version: str | None = None) -> dict:
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    inst = headless.pick(designer_version, for_file=path)
    out = headless.run({"command": "read", "path": path}, inst)
    out["saved_by"] = headless.file_designer_version(path)
    out["wires"] = [list(_wire_ends(w)) for w in out.get("wires", [])]
    for c in out.get("components", []):
        shown = control_pins(c.get("pins", []))
        if shown:
            c["control_pins"] = shown
    return out


def control_pins(pins: list[dict]) -> list[dict]:
    """The control pins added to a component, in the spec's form: [{"control": "mute", "dir": "both"}, ...]."""
    dirs: dict[str, set[str]] = {}
    for p in pins:
        if _is_added(p):
            dirs.setdefault(p["control"], set()).add(p["dir"])
    return [{"control": k, "dir": "both" if len(v) > 1 else next(iter(v))} for k, v in dirs.items()]


def summarise(design: dict, components: list[str] | None = None, include: set[str] | None = None) -> dict:
    """Trim a read-back for display: optional component filter and sections (signals come with wires)."""
    include = include or {"properties", "values", "pins", "wires", "signals", "graphics", "ucis", "settings", "snapshots"}
    d = {k: design[k] for k in ("path", "bytes", "designer", "saved_by", "core", "design", "usb_slots", "inventory", "pages")
         if k in design}
    if "settings" not in include:   # hardware settings: the Core's, the design's and each inventory item's
        d.pop("design", None)
        if isinstance(d.get("core"), dict):
            d["core"] = {k: v for k, v in d["core"].items() if k != "settings"}
        if "inventory" in d:
            d["inventory"] = [{k: v for k, v in i.items() if k != "settings"} for i in d["inventory"]]
    comps = design["components"]
    if components:
        comps = [c for c in comps if c["name"] in components]
    d["component_count"] = len(design["components"])
    d["components"] = [{k: v for k, v in c.items() if k not in {"properties", "values", "pins"} or k in include} for c in comps]
    snaps = design.get("snapshots")
    if "snapshots" in include and snaps is not None:
        if components and "banks" in snaps:
            snaps = {**snaps, "banks": [b for b in snaps["banks"] if b.get("name") in components
                                        or any(m.get("component") in components for m in b.get("members", []))]}
        d["snapshots"] = snaps
    if "wires" in include:
        d["wires"] = design["wires"] if not components else [w for w in design["wires"]
                                                             if any(w[0].startswith(n + ".") or w[1].startswith(n + ".") for n in components)]
    d["wire_count"] = len(design["wires"])
    control = {_pid(c, p) for c in design["components"] for p in c.get("pins", []) if _is_control(p)}
    d["control_wire_count"] = sum(1 for w in design["wires"] if w[0] in control or w[1] in control)
    if "signals" in design and include & {"wires", "signals"}:
        d["signals"] = design["signals"] if not components else [
            s for s in design["signals"] if any(r and r.startswith(n + ".") for n in components
                                                for r in [s.get("source"), *s["targets"], *s.get("extra_sources", [])])]
    if "graphics" in design and "graphics" in include:
        d["graphics"] = design["graphics"]
    ucis = design.get("ucis", [])
    d["ucis"] = ucis if "ucis" in include else [_uci_outline(u) for u in ucis]
    return d


def _uci_outline(u: dict) -> dict:
    """A UCI without its elements: name, size, page names and how many elements there are."""
    out = {k: u[k] for k in ("name", "width", "height") if k in u}
    out["pages"] = [p.get("name") for p in u.get("pages", [])]
    out["elements"] = sum(len(p.get("elements", [])) for p in u.get("pages", []))
    return out


# ---- signal checks ------------------------------------------------------------------------

def _is_control(p: dict) -> bool:
    return p.get("domain") == "control"


def _is_added(p: dict) -> bool:
    """A control pin added for a control, rather than one of the block's own."""
    return _is_control(p) and bool(p.get("control")) and bool(p.get("user_created"))


def _pid(c: dict, p: dict) -> str:
    return f"{c['name']}.{p['dir']}.{p['index']}"


def _wire_ends(w: list[str]) -> tuple[str, str]:
    """A wire as (output, input). Designer saves a wire drawn from an input to an output the other way round."""
    a, b = w
    ma, mb = ENDPOINT_RE.match(a or ""), ENDPOINT_RE.match(b or "")
    if ma and mb and ma["dir"] == "in" and mb["dir"] == "out":
        return b, a
    return a, b


def _graph(design: dict):
    by_name = {c["name"]: c for c in design["components"]}
    edges: dict[str, list[str]] = {}
    for w in design["wires"]:
        a, b = _wire_ends(w)
        edges.setdefault(a, []).append(b)
    for c in design["components"]:
        outs = [p for p in c["pins"] if p["dir"] == "out"]
        for i in (p for p in c["pins"] if p["dir"] == "in"):
            if (c["class"], i["name"]) in NON_PASSING:
                continue
            # a block passes each input to its outputs of the same kind: signal to signal, control to control
            edges.setdefault(_pid(c, i), []).extend(_pid(c, o) for o in outs if _is_control(o) == _is_control(i))
    for g in _signals(design).values():
        for s in g["sources"]:
            edges.setdefault(s, []).extend(g["targets"])
    return by_name, edges


def _signals(design: dict) -> dict[str, dict]:
    """Signal Names from the pins' labels: case-folded name -> {name, sources, targets} (pin refs).
    Q-SYS names aren't case sensitive; the group keeps its source's spelling."""
    groups: dict[str, dict] = {}
    for c in design["components"]:
        for p in c["pins"]:
            if not p.get("signal"):
                continue
            g = groups.setdefault(p["signal"].casefold(), {"name": p["signal"], "sources": [], "targets": []})
            if p["dir"] == "out" and not g["sources"]:
                g["name"] = p["signal"]
            g["sources" if p["dir"] == "out" else "targets"].append(f"{c['name']}.{p['dir']}.{p['index']}")
    return groups


def _pins(by_name: dict, ref: str, kind: str | None = None) -> list[str]:
    """'NAME' -> its pins (with kind 'signal' or 'control', only that kind when it has any);
    'NAME.in.N' / 'NAME.out.Name' -> that pin, by index, name or (a control pin) control code name."""
    m = ENDPOINT_RE.match(ref)
    name = m["name"] if m else ref
    if name not in by_name:
        raise SpecError(f"no component {name}")
    pins = by_name[name]["pins"]
    if not m:
        if kind:
            pins = [p for p in pins if _is_control(p) == (kind == "control")] or pins
        return [f"{name}.{p['dir']}.{p['index']}" for p in pins]
    want = m["pin"].lower()
    hit = [p for p in pins if p["dir"] == m["dir"] and (str(p["index"]) == m["pin"] or (p["name"] or "").lower() == want)]
    if not hit:
        hit = [p for p in pins if p["dir"] == m["dir"] and (p.get("control") or "").lower() == want]
    if not hit:
        raise SpecError(f"{name} has no {m['dir']} pin {m['pin']!r}")
    return [f"{name}.{p['dir']}.{p['index']}" for p in hit]


def _reach(edges: dict, start: list[str]) -> set[str]:
    seen, q = set(), deque(start)
    while q:
        p = q.popleft()
        if p in seen:
            continue
        seen.add(p)
        q.extend(edges.get(p, []))
    return seen


def check(design: dict, paths: list[dict] | None = None, allow_unconnected: list[str] | None = None) -> dict:
    """Static checks on a read-back design.

    paths: [{"from": "FLEX_IN.out.3", "to": "USB_TX_CONF", "expect": false, "via": ["MIC_MUTE"]}]
      'to' may be a component (any of its inputs) or a pin; expect defaults to true.
      AEC Reference inputs don't pass signal (the AEC subtracts them), so a far-end feed reaching
      a reference doesn't count as reaching the AEC's output.
      Signal and control stay apart: a block passes signal inputs to signal outputs and control inputs
      to control outputs. A rule from a control pin follows control wires, and then a bare component
      name in 'to' or 'via' means that block's control pins; otherwise it means its signal pins.
    allow_unconnected: pins or components whose unconnected pins are intended (spare I/O etc.).
    Unconnected signal pins are one check. Control pins added for controls are another: a control none
    of whose pins is wired fails it. A block's own control pins (logic inputs and the like) aren't checked.
    A Signal Name connects its source (the output carrying it) to every input carrying it, for the
    unconnected-pins check and for paths, and each name must have exactly one source."""
    by_name, edges = _graph(design)
    groups = _signals(design)
    results = []

    names = [c["name"] for c in design["components"]]
    dupes = sorted({n for n in names if names.count(n) > 1})
    results.append({"check": "component names unique", "pass": not dupes, "detail": dupes or f"{len(names)} names"})

    connected = {p for w in design["wires"] for p in w}
    for g in groups.values():
        if g["sources"] and g["targets"]:
            connected.update(g["sources"] + g["targets"])
    allowed = set()
    for a in allow_unconnected or []:
        allowed.update(_pins(by_name, a))
    loose = [f"{_pid(c, p)} ({p['name']})" for c in design["components"] for p in c["pins"]
             if not _is_control(p) and _pid(c, p) not in connected and _pid(c, p) not in allowed]
    results.append({"check": "no unexpected unconnected pins", "pass": not loose, "detail": loose[:60] or "none"})

    unused = []
    for c in design["components"]:
        shown: dict[str, list[dict]] = {}
        for p in c["pins"]:
            if _is_added(p):
                shown.setdefault(p["control"], []).append(p)
        for control, ps in shown.items():
            if not any(_pid(c, p) in connected or _pid(c, p) in allowed for p in ps):
                unused.append(f"{c['name']}.{control} ({'/'.join(p['dir'] for p in ps)})")
    results.append({"check": "no unused control pins", "pass": not unused, "detail": unused[:60] or "none"})

    bad = [f"{g['name']}: " + (f"{len(g['sources'])} sources ({', '.join(g['sources'])})" if g["sources"] else "no source")
           for g in groups.values() if len(g["sources"]) != 1]
    results.append({"check": "signal names: one source each", "pass": not bad,
                    "detail": bad or (f"{len(groups)} names" if groups else "none")})

    nan = [f"{c['name']}.{k}" for c in design["components"] for k, v in c.get("values", {}).items()
           if "value" in v and v.get("position") is None]
    results.append({"check": "every stored value has a position", "pass": not nan, "detail": nan or "ok"})

    control_pin = {_pid(c, p) for c in design["components"] for p in c["pins"] if _is_control(p)}
    for rule in paths or []:
        expect = rule.get("expect", True)
        start = _pins(by_name, rule["from"], "signal")
        kind = "control" if start and all(s in control_pin for s in start) else "signal"
        reached = _reach(edges, start)
        targets = _pins(by_name, rule["to"], kind)
        if ENDPOINT_RE.match(rule["to"]) is None:
            targets = [t for t in targets if ".in." in t]
        hit = any(t in reached for t in targets)
        via_missing = [v for v in rule.get("via", []) if not any(p in reached for p in _pins(by_name, v, kind))]
        ok = (hit == expect) and (not expect or not via_missing)
        label = rule.get("name") or f"{rule['from']} {'reaches' if expect else 'does NOT reach'} {rule['to']}"
        detail = {"reached": hit}
        if via_missing:
            detail["missing_via"] = via_missing
        results.append({"check": label, "pass": ok, "detail": detail})
    return {"passed": sum(r["pass"] for r in results), "failed": sum(not r["pass"] for r in results), "results": results}


# ---- snapshot values ----------------------------------------------------------------------

def _snapshot_members(members, where: str, problems: list[str]) -> dict[str, set[str] | None]:
    """A bank's members as {component: its controls, or None for every control of the block}."""
    out: dict[str, set[str] | None] = {}
    if not isinstance(members, list):
        problems.append(f"{where}: members must be a list")
        return out
    for m in members:
        if isinstance(m, str):
            comp, _, ctl = m.partition(".")
            if not comp or not ctl:
                problems.append(f"{where}: member {m!r} should look like COMPONENT.control")
                continue
            controls, whole = {ctl}, False
        elif isinstance(m, dict) and isinstance(m.get("component"), str):
            comp, whole, listed = m["component"], m.get("all") is True, m.get("controls") or []
            if whole == bool(listed) or not isinstance(listed, list) or not all(isinstance(c, str) and c for c in listed):
                problems.append(f"{where}: member {comp}: give a list of control names or all: true")
                continue
            controls = set(listed)
        else:
            problems.append(f"{where}: member {m!r} should be COMPONENT.control or {{component, controls | all}}")
            continue
        if comp in out and (out[comp] is None) != whole:
            problems.append(f"{where}: {comp} is listed both whole (all: true) and by control")
            continue
        out[comp] = None if whole else (out.get(comp) or set()) | controls
    return out


def _validate_snapshots(spec: dict, names: set[str], pages: list[str], problems: list[str]) -> set[str]:
    """Checks spec.snapshots; returns the bank names (each is also its controller block's code name)."""
    banks: set[str] = set()
    for i, b in enumerate(spec.get("snapshots") or []):
        where = f"snapshots[{i}]"
        if not isinstance(b, dict):
            problems.append(f"{where}: expected an object with name, count and members")
            continue
        name = b.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            problems.append(f"{where}: name {name!r} must start with a letter and use letters, digits, _ or -")
        elif name in names or name in banks:
            problems.append(f"{where}: name {name} is already used (a bank's name is its controller's code name)")
        else:
            where = f"snapshot bank {name}"
        if isinstance(name, str):
            banks.add(name)
        count = b.get("count")
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            problems.append(f"{where}: count must be a whole number of snapshots, 1 or more")
            count = None
        members = _snapshot_members(b.get("members", []), where, problems)
        problems.extend(f"{where}: no component {c}" for c in members if c not in names)
        ctl = b.get("controller") or {}
        if not isinstance(ctl, dict):
            problems.append(f"{where}: controller must be an object (page, x, y, label, script_access)")
            ctl = {}
        if ctl.get("page", pages[0]) not in pages:
            problems.append(f"{where}: controller page {ctl.get('page')!r} is not in spec.pages")
        if ctl.get("script_access", "None") not in ACCESS:
            problems.append(f"{where}: controller script_access must be one of {sorted(ACCESS)}")
        values = b.get("values") or {}
        if not isinstance(values, dict):
            problems.append(f"{where}: values must map snapshot numbers to {{COMPONENT.control: value}}")
            continue
        for n, snap in values.items():
            if not str(n).isdigit() or int(n) < 1 or (count and int(n) > count):
                problems.append(f"{where}: values key {n!r} must be a snapshot number from 1 to {count or 'count'}")
                continue
            if not isinstance(snap, dict):
                problems.append(f"{where}: snapshot {n} must map COMPONENT.control to a value")
                continue
            for key, v in snap.items():
                comp, _, control = key.partition(".")
                if comp not in members or not control or (members[comp] is not None and control not in members[comp]):
                    problems.append(f"{where}: snapshot {n}: {key} is not a member of the bank")
                elif not isinstance(v, (bool, int, float, str)):
                    problems.append(f"{where}: snapshot {n}: {key} must be a number, true/false or a string")
    return banks


def snapshot_values(spec_or_values) -> dict[str, dict[int, dict]]:
    """Snapshot values as {bank: {n: {"COMP.control": value}}}, from a spec, its "snapshots" list, or
    that mapping itself."""
    src = spec_or_values
    if isinstance(src, dict) and "snapshots" in src:
        src = src["snapshots"]
    elif isinstance(src, dict) and src.keys() & {"components", "wires", "ucis", "pages", "core", "inventory"}:
        raise SpecError("the spec has no snapshots")
    if isinstance(src, list):
        src = {b.get("name"): b.get("values") or {} for b in src if isinstance(b, dict)}
    if not isinstance(src, dict):
        raise SpecError("snapshot values must be a spec, its snapshots list, or {BANK: {n: {COMP.control: value}}}")
    out: dict[str, dict[int, dict]] = {}
    for bank, snaps in src.items():
        if not isinstance(snaps, dict):
            raise SpecError(f"snapshot bank {bank}: values must map snapshot numbers to {{COMP.control: value}}")
        for n, vals in snaps.items():
            if not str(n).isdigit() or not isinstance(vals, dict):
                raise SpecError(f"snapshot bank {bank}: {n!r} must be a snapshot number mapped to {{COMP.control: value}}")
            out.setdefault(bank, {})[int(n)] = dict(vals)   # {} is a snapshot of the design's own values
    return out


def snapshot_plan(design: dict, values: dict[str, dict[int, dict]]) -> tuple[list[dict], list[str]]:
    """Matches snapshot values to a read-back design's banks. Returns
    ([{bank, controller, count, snapshots: {n: [(component, control, value)]}}], problems)."""
    if "snapshots" not in design:
        return [], ["this Designer's helper doesn't read snapshot banks yet: rebuild the .NET 8 helper (README, Development)"]
    snaps = design.get("snapshots") or {}
    banks = {b["name"]: b for b in snaps.get("banks", [])}
    plan, problems = [], []
    if snaps.get("error"):
        problems.append(f"could not read the design's snapshot banks: {snaps['error']}")
    for name, by_number in values.items():
        b = banks.get(name)
        if b is None:
            problems.append(f"no snapshot bank {name} in the design (banks: {sorted(banks) or 'none'})")
            continue
        controller = (b.get("controller") or {}).get("name")
        if not controller:
            problems.append(f"snapshot bank {name} has no controller block")
            continue
        whole = {m["component"] for m in b.get("members", []) if m.get("all")}
        listed = {(m["component"], c) for m in b.get("members", []) for c in m.get("controls", [])}
        entry = {"bank": name, "controller": controller, "count": b.get("count"), "snapshots": {}}
        for n, vals in sorted(by_number.items()):
            if not 1 <= n <= (b.get("count") or 0):
                problems.append(f"snapshot bank {name}: no snapshot {n} (it has {b.get('count')})")
                continue
            sets = []
            for key, v in vals.items():
                comp, _, control = key.partition(".")
                if not control or (comp not in whole and (comp, control) not in listed):
                    problems.append(f"snapshot bank {name}: snapshot {n}: {key} is not a member of the bank")
                else:
                    sets.append((comp, control, v))
            entry["snapshots"][n] = sets
        if entry["snapshots"]:
            plan.append(entry)
    return plan, problems
