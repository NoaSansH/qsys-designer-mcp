"""Reusable spec fragments ("templates"), included into a spec as many times as needed.

spec["include"] = [{"file": "room_template.json", "prefix": "R2_", "params": {"ROOM": "Room 2"},
                    "dx": 0, "dy": 900, "page": "Room 2"}]

A template is a JSON file with any of components, wires, signals, graphics, snapshot_banks,
named_controls and ucis, plus optional "params" defaults. In it, "${NAME}" is replaced by the param's
value; every block, bank, controller and named control gets the prefix, wire and signal endpoints and
signal names follow, and positions move by dx/dy. Paths are relative to the including spec's folder.

A component marked "shared": true keeps its name and is placed once however many times the template is
included (a Core's Flex Out, a device): later copies only add their values and control pins to it.
{"template": "speaker_zone"} takes one of the templates that come with the server (see library()).
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from qsys_mcp.designs import ENDPOINT_RE, SpecError

PARAM_RE = re.compile(r"\$\{(\w+)\}")
MERGED = ("components", "wires", "signals", "graphics", "snapshot_banks", "named_controls", "ucis", "inventory")
LIBRARY = Path(__file__).parent / "templates_lib"


def library() -> list[dict]:
    """The templates that come with the server: name, description, params and their defaults."""
    out = []
    for f in sorted(LIBRARY.glob("*.json")):
        t = json.loads(f.read_text(encoding="utf-8"))
        out.append({"name": f.stem, "description": t.get("description", ""), "params": t.get("params", {}),
                    "param_help": t.get("param_help", {}), "zones": t.get("zones", {}),
                    "components": [c["name"] for c in t.get("components", [])]})
    return out


def _subst(v, params: dict, where: str):
    if isinstance(v, str):
        def one(m):
            if m.group(1) not in params:
                raise SpecError(f"{where}: no value for ${{{m.group(1)}}}")
            return str(params[m.group(1)])
        # a string that is exactly one param keeps the param's type (a number stays a number)
        whole = PARAM_RE.fullmatch(v)
        return params[whole.group(1)] if whole and whole.group(1) in params else PARAM_RE.sub(one, v)
    if isinstance(v, list):
        return [_subst(x, params, where) for x in v]
    if isinstance(v, dict):   # keys too: "channel_${CH}_mute"
        return {_subst(k, params, where): _subst(x, params, where) for k, x in v.items()}
    return v


def _endpoint(e: str, names: set[str], prefix: str) -> str:
    m = ENDPOINT_RE.match(e or "")
    return f"{prefix}{m['name']}.{m['dir']}.{m['pin']}" if m and m["name"] in names else e


def expand(spec: dict, base_dir: str | Path | None = None, _depth: int = 0) -> dict:
    """Return the spec with its includes merged in (templates may include templates)."""
    if not spec.get("include"):
        return spec
    if _depth > 8:
        raise SpecError("templates include each other too deeply (a loop?)")
    spec = copy.deepcopy(spec)
    base = Path(base_dir) if base_dir else Path.cwd()
    for i, inc in enumerate(spec.pop("include")):
        where = f"include[{i}]"
        if "template" in inc:   # one of the server's own templates, by name
            f = LIBRARY / (inc["template"] + ".json")
            if not f.is_file():
                raise SpecError(f"{where}: no template {inc['template']!r}; templates: {[t['name'] for t in library()]}")
        else:
            f = Path(inc["file"])
            f = f if f.is_absolute() else base / f
        try:
            tpl = json.loads(f.read_text(encoding="utf-8"))
        except OSError as e:
            raise SpecError(f"{where}: cannot read {f}: {e}") from e
        tpl = expand(tpl, f.parent, _depth + 1)
        params = {**tpl.pop("params", {}), **inc.get("params", {})}
        for k in ("description", "param_help"):
            tpl.pop(k, None)
        tpl = _subst(tpl, params, f"{where} ({f.name})")
        prefix, dx, dy, page = inc.get("prefix", ""), inc.get("dx", 0), inc.get("dy", 0), inc.get("page")
        names = {c["name"] for c in tpl.get("components", []) if not c.get("shared")}
        for b in tpl.get("snapshot_banks", []):
            if b.get("controller"):
                names.add(b["controller"].get("name", b["name"]))

        def move(o):
            if "x" in o:
                o["x"] += dx
            if "y" in o:
                o["y"] += dy
            if page and "page" not in o:
                o["page"] = page

        existing = {c["name"]: c for c in spec.get("components", [])}
        kept = []
        for c in tpl.get("components", []):
            if c.pop("shared", False):
                if c["name"] in existing:      # already placed: add this copy's settings to it
                    first = existing[c["name"]]
                    first["values"] = {**c.get("values", {}), **first.get("values", {})}
                    pins = first.setdefault("control_pins", [])
                    pins.extend(x for x in c.get("control_pins", []) if x not in pins)
                    continue
            else:
                c["name"] = prefix + c["name"]
            move(c)
            kept.append(c)
        tpl["components"] = kept
        for key in ("wires", "signals"):
            out = []
            for w in tpl.get(key, []):
                if isinstance(w, list):
                    w = [_endpoint(w[0], names, prefix), _endpoint(w[1], names, prefix)] + \
                        ([prefix + w[2]] if len(w) > 2 and w[2] else [])
                else:
                    w = {**w, "from": _endpoint(w["from"], names, prefix), "to": _endpoint(w["to"], names, prefix)}
                    if w.get("signal"):
                        w["signal"] = prefix + w["signal"]
                out.append(w)
            tpl[key] = out
        for g in tpl.get("graphics", []):
            move(g)
        for b in tpl.get("snapshot_banks", []):
            b["name"] = prefix + b["name"]
            for m in b.get("controls", []):
                m["component"] = prefix + m["component"] if m["component"] in names else m["component"]
            if b.get("controller"):
                b["controller"]["name"] = prefix + b["controller"].get("name", b["name"][len(prefix):])
                move(b["controller"])
        for n in tpl.get("named_controls", []):
            n["name"] = prefix + n["name"]
            n["component"] = prefix + n["component"] if n["component"] in names else n["component"]
        for u in tpl.get("ucis", []):
            u["name"] = prefix + u["name"]
            for p in u.get("pages", []):
                for e in p.get("elements", []):
                    if e.get("component") in names:
                        e["component"] = prefix + e["component"]
        known = {i.get("name") for i in spec.get("inventory", [])}
        tpl["inventory"] = [i for i in tpl.get("inventory", []) if i.get("name") not in known]   # shared devices once
        for key in MERGED:
            spec.setdefault(key, []).extend(tpl.get(key, []))
        for k, v in tpl.get("core", {}).items():          # Core settings the template needs, unless the spec sets them
            spec.setdefault("core", {}).setdefault(k, v)
        for z, style in tpl.get("zones", {}).items():
            spec.setdefault("zones", {}).setdefault(prefix + z if prefix else z, style)
        for c in tpl.get("components", []):
            if c.get("zone") and prefix:
                c["zone"] = prefix + c["zone"]
        if page and page not in spec.setdefault("pages", spec.get("pages") or ["Main"]):
            spec["pages"].append(page)
    return spec
