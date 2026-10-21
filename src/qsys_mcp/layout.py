"""Automatic schematic layout: blocks in columns along the signal flow, notes under blocks, zones.

Designer sizes blocks itself (from the label and the pins), and only when it draws them. Sizes it
has drawn are learned from saved designs (`learn_sizes`) and cached; anything else is estimated.

Spec keys used here:
  spec["layout"] = "auto" (place every block) or omitted (place only blocks without x/y),
                   or {"mode": "auto", "x": 40, "y": 40, "column_gap": 110, "row_gap": 30}
  component["zone"] = "INPUTS"    blocks of a zone are kept together, framed and titled
  component["note"] = "text"      a note under the block
  spec["zones"] = {"INPUTS": {"fill": "#40D32F2F", "text": "short description"}}   optional styling
"""

from __future__ import annotations

import copy
import json
import re
from collections import defaultdict

from qsys_mcp import headless

NOTE_FILL = "#FFFFF4C2"
ZONE_FILLS = ["#40D32F2F", "#4043A047", "#401E88E5", "#40FB8C00", "#408E24AA", "#4000ACC1", "#40FFB300", "#40757575"]
ENDPOINT_RE = re.compile(r"^(?P<name>.+)\.(?P<dir>in|out)\.(?P<pin>.+)$")


# ---- sizes --------------------------------------------------------------------------------------

def _sizes_path(inst: headless.Install):
    return headless.cache_dir() / f"block-sizes-{inst.version}.json"


def load_sizes(inst: headless.Install) -> dict:
    p = _sizes_path(inst)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def size_key(cls: str, label: str, ins: int, outs: int) -> str:
    return f"{cls}|{label}|{ins}|{outs}"


def learn_sizes(inst: headless.Install, design: dict) -> int:
    """Remember the sizes Designer gave the blocks of a design it saved (read_design output)."""
    sizes = load_sizes(inst)
    n = 0
    for c in design["components"]:
        w, h = c.get("w"), c.get("h")
        if not w or not h or (w == 80 and h == 80):   # 80 x 80: never drawn by Designer
            continue
        ins = sum(p["dir"] == "in" for p in c["pins"])
        outs = sum(p["dir"] == "out" for p in c["pins"])
        sizes[size_key(c["class"], c["label"], ins, outs)] = [w, h]
        sizes.setdefault(f"{c['class']}|*", [w, h])
        n += 1
    _sizes_path(inst).write_text(json.dumps(sizes, indent=0), encoding="utf-8")
    return n


def estimate(c: dict, sizes: dict) -> tuple[float, float]:
    """A block's size: learned for this class and label if known, else from its label and pins."""
    label = c.get("label") or c["name"]
    pins = len(c.get("control_pins", []))
    for key in (k for k in sizes if k.startswith(f"{c.get('class', '')}|{label}|")):
        return tuple(sizes[key])
    lines = label.split("\n")
    w = min(max(82, 8 * max(len(l) for l in lines)), 180)
    io = max(c.get("_ins", 1), c.get("_outs", 1)) + pins
    h = max(40, 14 * len(lines) + 9 * io + 8)
    return w, h


# ---- graph --------------------------------------------------------------------------------------

def _links(spec: dict) -> list[tuple[str, str]]:
    out = []
    for w in spec.get("wires", []) + spec.get("signals", []):
        a, b = (w[0], w[1]) if isinstance(w, list) else (w.get("from"), w.get("to"))
        ma, mb = ENDPOINT_RE.match(a or ""), ENDPOINT_RE.match(b or "")
        if ma and mb and ma["name"] != mb["name"]:
            out.append((ma["name"], mb["name"]))
    return out


def _columns(names: list[str], links: list[tuple[str, str]]) -> dict[str, int]:
    """Column of each block: the longest path to it from a block nothing feeds (back edges ignored)."""
    succ = defaultdict(list)
    for a, b in links:
        if a in names and b in names:
            succ[a].append(b)
    order, state = [], {}

    def visit(n):   # depth-first, dropping edges that close a loop
        state[n] = 1
        for m in list(succ[n]):
            if state.get(m) == 1:
                succ[n].remove(m)
            elif m not in state:
                visit(m)
        state[n] = 2
        order.append(n)

    fed = {b for a, b in links}
    for n in [n for n in names if n not in fed] + names:
        if n not in state:
            visit(n)
    col = {n: 0 for n in names}
    for n in reversed(order):
        for m in succ[n]:
            col[m] = max(col[m], col[n] + 1)
    return col


# ---- layout -------------------------------------------------------------------------------------

def auto_layout(spec: dict, sizes: dict | None = None) -> dict:
    """Return a copy of the spec with x/y on its blocks, notes under blocks and zone frames."""
    opts = spec.get("layout")
    if not opts:
        if all("x" in c and "y" in c for c in spec.get("components", [])) and \
                not any("note" in c or "zone" in c for c in spec.get("components", [])):
            return spec
        opts = {"mode": "missing"}
    if isinstance(opts, str):
        opts = {"mode": opts}
    spec = copy.deepcopy(spec)
    sizes = sizes or {}
    x0, y0 = opts.get("x", 40), opts.get("y", 40)
    col_gap, row_gap = opts.get("column_gap", 110), opts.get("row_gap", 30)
    note_w, zone_pad = opts.get("note_width", 150), 18
    place_all = opts.get("mode") == "auto"
    pages = spec.get("pages") or ["Main"]
    links = _links(spec)
    ins, outs = defaultdict(int), defaultdict(int)
    for a, b in links:
        outs[a] += 1
        ins[b] += 1
    graphics = spec.setdefault("graphics", [])
    zones_style = spec.get("zones", {})

    for page in pages:
        comps = [c for c in spec.get("components", []) if c.get("page", pages[0]) == page]
        movable = [c for c in comps if place_all or "x" not in c or "y" not in c]
        if not movable and not any("note" in c for c in comps):
            continue
        for c in comps:
            c["_ins"], c["_outs"] = ins[c["name"]], outs[c["name"]]
            c["_w"], c["_h"] = estimate(c, sizes)
        names = [c["name"] for c in movable]
        col = _columns(names, links)
        # zones keep their blocks together: each zone is a band of columns, in order of first column
        zone_of = {c["name"]: c.get("zone", "") for c in movable}
        zones = sorted({z for z in zone_of.values()}, key=lambda z: min(col[n] for n in names if zone_of[n] == z))
        by_col = defaultdict(list)
        for n in names:
            by_col[(zones.index(zone_of[n]), col[n])].append(n)
        keys = sorted(by_col)
        # order rows by the average row of what feeds each block (fewer crossings)
        row_of, pos = {}, {}
        for k in keys:
            def bary(n):
                feeders = [row_of[a] for a, b in links if b == n and a in row_of]
                return sum(feeders) / len(feeders) if feeders else 1e9
            for i, n in enumerate(sorted(by_col[k], key=lambda n: (bary(n), names.index(n)))):
                row_of[n] = i
        cx = x0
        by_name = {c["name"]: c for c in comps}
        zone_box = {}
        current_zone = None
        for k in keys:
            zi, _ = k
            if current_zone is not None and zi != current_zone:
                cx += zone_pad * 2
            current_zone = zi
            colw = max(max(by_name[n]["_w"], note_w if "note" in by_name[n] else 0) for n in by_col[k])
            cy = y0 + (40 if zones != [""] else 0)
            for n in sorted(by_col[k], key=lambda n: row_of[n]):
                c = by_name[n]
                c["x"], c["y"] = cx, cy
                cy += c["_h"] + row_gap + (_note_h(c) + 6 if "note" in c else 0)
                zb = zone_box.setdefault(zone_of[n], [cx, y0, cx, cy])
                zb[0], zb[2], zb[3] = min(zb[0], cx), max(zb[2], cx + colw), max(zb[3], cy)
            cx += colw + col_gap
        for c in comps:
            if "note" in c:
                graphics.append({"type": "text", "text": c["note"], "x": c["x"], "y": c["y"] + c["_h"] + 4,
                                 "w": max(c["_w"], note_w), "h": _note_h(c), "font_size": 8.5, "align": "Left",
                                 "fill": NOTE_FILL, "z": -10, "page": page})
        for i, z in enumerate(zones):
            if not z:
                continue
            x1, y1, x2, y2 = zone_box[z]
            style = zones_style.get(z, {})
            graphics.append({"type": "group", "text": z, "x": x1 - zone_pad, "y": y1 - 10, "w": x2 - x1 + 2 * zone_pad,
                             "h": y2 - y1 + 10, "fill": style.get("fill", ZONE_FILLS[i % len(ZONE_FILLS)]),
                             "bold": True, "font_size": 12, "page": page})
            if style.get("text"):
                graphics.append({"type": "text", "text": style["text"], "x": x1 - zone_pad + 8, "y": y1 + 10,
                                 "w": x2 - x1 + 2 * zone_pad - 16, "h": 24, "font_size": 9, "align": "Left",
                                 "z": -20, "page": page})
        for c in comps:
            for k in ("_ins", "_outs", "_w", "_h"):
                c.pop(k, None)
    for c in spec.get("components", []):
        c.pop("note", None)
        c.pop("zone", None)
    spec.pop("zones", None)
    spec.pop("layout", None)
    return spec


def _note_h(c: dict) -> float:
    return 6 + 11 * (c["note"].count("\n") + 1)
