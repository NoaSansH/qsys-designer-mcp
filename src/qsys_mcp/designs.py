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


class SpecError(ValueError):
    pass


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
    out["wires"] = [list(_wire_ends(w)) for w in out.get("wires", [])]
    return out


def summarise(design: dict, components: list[str] | None = None, include: set[str] | None = None) -> dict:
    """Trim a read-back for display: optional component filter and sections."""
    include = include or {"properties", "values", "pins", "wires", "ucis"}
    d = {k: design[k] for k in ("path", "bytes", "designer", "saved_by", "core", "usb_slots", "inventory", "pages") if k in design}
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
