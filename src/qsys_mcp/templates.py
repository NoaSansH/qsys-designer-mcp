"""Reusable spec fragments ("templates"), included into a spec as many times as needed.

spec["include"] = [{"file": "room_template.json", "prefix": "R2_", "params": {"ROOM": "Room 2"},
                    "dx": 0, "dy": 900, "page": "Room 2"}]

A template is a JSON file with any of components, wires, signals, graphics, snapshot_banks,
named_controls and ucis, plus optional "params" defaults. In it, "${NAME}" is replaced by the param's
value; every block, bank, controller and named control gets the prefix, wire and signal endpoints and
signal names follow, and positions move by dx/dy. Paths are relative to the including spec's folder.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from qsys_mcp.designs import ENDPOINT_RE, SpecError

PARAM_RE = re.compile(r"\$\{(\w+)\}")
MERGED = ("components", "wires", "signals", "graphics", "snapshot_banks", "named_controls", "ucis")


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
    if isinstance(v, dict):
        return {k: _subst(x, params, where) for k, x in v.items()}
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
        f = Path(inc["file"])
        f = f if f.is_absolute() else base / f
        try:
            tpl = json.loads(f.read_text(encoding="utf-8"))
        except OSError as e:
            raise SpecError(f"{where}: cannot read {f}: {e}") from e
        tpl = expand(tpl, f.parent, _depth + 1)
        params = {**tpl.pop("params", {}), **inc.get("params", {})}
        tpl = _subst(tpl, params, f"{where} ({f.name})")
        prefix, dx, dy, page = inc.get("prefix", ""), inc.get("dx", 0), inc.get("dy", 0), inc.get("page")
        names = {c["name"] for c in tpl.get("components", [])}
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

        for c in tpl.get("components", []):
            c["name"] = prefix + c["name"]
            move(c)
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
        for key in MERGED:
            spec.setdefault(key, []).extend(tpl.get(key, []))
        if page and page not in spec.setdefault("pages", spec.get("pages") or ["Main"]):
            spec["pages"].append(page)
    return spec
