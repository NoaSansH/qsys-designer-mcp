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
# inputs that never pass signal to the block's outputs (the AEC subtracts its reference)
NON_PASSING = {("acoustic_echo_canceler_simd", "Reference"), ("acoustic_echo_canceler", "Reference")}
USB_TYPES = ("speakerphone", "soundcard", "none")
USB_KEYS = {"type", "count", "mode", "video_bridge", "set", "slots"}
CORE_KEYS = {"model", "name", "set", "usb"}
ITEM_KEYS = {"type", "name", "set", "usb"}
INVENTORY_REF_KEYS = {"item", "placeable", "class"}
OLD_HELPER = ("This Designer uses the prebuilt .NET helper, which is older than the hardware settings (core.set, "
              "core.usb, inventory usb, design). Rebuild it: dotnet publish helper-net8 -c Release -o src/qsys_mcp/helper/net8")


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
    for i, w in enumerate(spec.get("wires", [])):
        ends = w if isinstance(w, list) else [w.get("from"), w.get("to")]
        if len(ends) != 2:
            problems.append(f"wires[{i}]: expected [from, to]")
            continue
        for end, d in zip(ends, ("out", "in")):
            m = ENDPOINT_RE.match(end or "")
            if not m or m["dir"] != d:
                problems.append(f"wires[{i}]: {end!r} should look like NAME.{d}.N or NAME.{d}.PinName")
            elif m["name"] not in names:
                problems.append(f"wires[{i}]: no component {m['name']}")
    for u in spec.get("ucis", []):
        for p in u.get("pages", []):
            for j, e in enumerate(p.get("elements", [])):
                if e.get("type", "control") == "control" and e.get("component") not in names:
                    problems.append(f"UCI {u.get('name')}/{p.get('name')} element {j}: no component {e.get('component')}")
    _check_hardware(spec, problems)
    return spec, problems


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
    res.pop("values", None) if len(res.get("values", [])) > 200 else None
    return res


def read(path: str, designer_version: str | None = None) -> dict:
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    inst = headless.pick(designer_version, for_file=path)
    out = headless.run({"command": "read", "path": path}, inst)
    out["saved_by"] = headless.file_designer_version(path)
    return out


def summarise(design: dict, components: list[str] | None = None, include: set[str] | None = None) -> dict:
    """Trim a read-back for display: optional component filter and sections."""
    include = include or {"properties", "values", "pins", "wires", "ucis", "settings"}
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
    if "wires" in include:
        d["wires"] = design["wires"] if not components else [w for w in design["wires"]
                                                             if any(w[0].startswith(n + ".") or w[1].startswith(n + ".") for n in components)]
    d["wire_count"] = len(design["wires"])
    if "ucis" in include:
        d["ucis"] = design["ucis"]
    return d


# ---- signal checks ------------------------------------------------------------------------

def _graph(design: dict):
    by_name = {c["name"]: c for c in design["components"]}
    edges: dict[str, list[str]] = {}
    for a, b in design["wires"]:
        edges.setdefault(a, []).append(b)
    for c in design["components"]:
        ins = [p for p in c["pins"] if p["dir"] == "in"]
        outs = [p for p in c["pins"] if p["dir"] == "out"]
        for i in ins:
            if (c["class"], i["name"]) in NON_PASSING:
                continue
            edges.setdefault(f"{c['name']}.in.{i['index']}", []).extend(f"{c['name']}.out.{o['index']}" for o in outs)
    return by_name, edges


def _pins(by_name: dict, ref: str) -> list[str]:
    """'NAME' -> all its pins; 'NAME.in.N' / 'NAME.out.Name' -> that pin (by index or name)."""
    m = ENDPOINT_RE.match(ref)
    name = m["name"] if m else ref
    if name not in by_name:
        raise SpecError(f"no component {name}")
    pins = by_name[name]["pins"]
    if not m:
        return [f"{name}.{p['dir']}.{p['index']}" for p in pins]
    hit = [p for p in pins if p["dir"] == m["dir"] and (str(p["index"]) == m["pin"] or p["name"].lower() == m["pin"].lower())]
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
    allow_unconnected: pins or components whose unconnected pins are intended (spare I/O etc.)."""
    by_name, edges = _graph(design)
    results = []

    names = [c["name"] for c in design["components"]]
    dupes = sorted({n for n in names if names.count(n) > 1})
    results.append({"check": "component names unique", "pass": not dupes, "detail": dupes or f"{len(names)} names"})

    connected = {p for w in design["wires"] for p in w}
    allowed = set()
    for a in allow_unconnected or []:
        allowed.update(_pins(by_name, a))
    loose = [f"{c['name']}.{p['dir']}.{p['index']} ({p['name']})" for c in design["components"] for p in c["pins"]
             if f"{c['name']}.{p['dir']}.{p['index']}" not in connected and f"{c['name']}.{p['dir']}.{p['index']}" not in allowed]
    results.append({"check": "no unexpected unconnected pins", "pass": not loose, "detail": loose[:60] or "none"})

    nan = [f"{c['name']}.{k}" for c in design["components"] for k, v in c.get("values", {}).items()
           if "value" in v and v.get("position") is None]
    results.append({"check": "every stored value has a position", "pass": not nan, "detail": nan or "ok"})

    for rule in paths or []:
        expect = rule.get("expect", True)
        start = _pins(by_name, rule["from"])
        reached = _reach(edges, start)
        targets = _pins(by_name, rule["to"])
        if ENDPOINT_RE.match(rule["to"]) is None:
            targets = [t for t in targets if ".in." in t]
        hit = any(t in reached for t in targets)
        via_missing = [v for v in rule.get("via", []) if not any(p in reached for p in _pins(by_name, v))]
        ok = (hit == expect) and (not expect or not via_missing)
        label = rule.get("name") or f"{rule['from']} {'reaches' if expect else 'does NOT reach'} {rule['to']}"
        detail = {"reached": hit}
        if via_missing:
            detail["missing_via"] = via_missing
        results.append({"check": label, "pass": ok, "detail": detail})
    return {"passed": sum(r["pass"] for r in results), "failed": sum(not r["pass"] for r in results), "results": results}
