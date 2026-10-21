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
        sizes[size_key(c["class"], c.get("user_label") or c["label"], ins, outs)] = [w, h]
        sizes.setdefault(f"{c['class']}|*", [w, h])
        n += 1
    _sizes_path(inst).write_text(json.dumps(sizes, indent=0), encoding="utf-8")
    return n


ROW = 10   # a control pin row listed under a block


def _rows(c: dict) -> int:
    """Control pins Designer lists under the block (its saved size leaves them out)."""
    tc = c.get("text_controller") or {}
    return len(c.get("control_pins", [])) + sum(1 for x in tc.get("controls", []) if x.get("pins", "None") != "None")


def estimate(c: dict, sizes: dict) -> tuple[float, float]:
    """A block's size: learned for this class and label if known, else from its label and pins,
    plus the rows of its control pins."""
    label = c.get("label") or c["name"]
    extra = ROW * _rows(c)
    cls = c.get("class")   # inventory blocks have no class in the spec: match on the label alone
    for key in (k for k in sizes if (k.startswith(f"{cls}|{label}|") if cls else f"|{label}|" in k)):
        w, h = sizes[key]
        return w, h + extra
    lines = label.split("\n")
    w = min(max(82, 8 * max(len(l) for l in lines)), 180)
    io = max(c.get("_ins", 1), c.get("_outs", 1))
    h = max(24, 14 * len(lines) + 8 * io)
    return w, h + extra


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
    """Return a copy of the spec with x/y on its blocks, notes under blocks and zone frames.

    Columns follow the signal flow and are shared by the whole page. Each zone covers the band of
    columns its blocks sit in; zones are placed in reading order (the order of spec["zones"], else
    of first appearance), each under the zones already placed in its band. Snapshot Controllers without a position join a zone named after their bank's
    "zone" key, or the last column."""
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
    col_gap, row_gap = opts.get("column_gap", 90), opts.get("row_gap", 26)
    note_w, pad, zone_gap = opts.get("note_width", 130), 16, 24
    title_h = 44   # zone title and its one-line description
    place_all = opts.get("mode") == "auto"
    pages = spec.get("pages") or ["Main"]
    graphics = spec.setdefault("graphics", [])
    zones_style = spec.get("zones", {})

    # Snapshot Controllers are placed like blocks
    ctrls = []
    for b in spec.get("snapshot_banks", []):
        c = b.get("controller")
        if c is not None and (place_all or "x" not in c or "y" not in c):
            ctrls.append({"name": c.get("name", b["name"]), "class": "snapshot_controller", "label": c.get("label", b["name"]),
                          "zone": b.get("zone", ""), "page": c.get("page"), "_ctrl": c,
                          **({"note": b["note"]} if "note" in b else {})})
    blocks = spec.get("components", []) + ctrls
    links = _links(spec)
    ins, outs = defaultdict(int), defaultdict(int)
    for a, b in links:
        outs[a] += 1
        ins[b] += 1

    for page in pages:
        comps = [c for c in blocks if (c.get("page") or pages[0]) == page]
        movable = [c for c in comps if place_all or "_ctrl" in c or "x" not in c or "y" not in c]
        for c in comps:
            c["_ins"], c["_outs"] = ins[c["name"]], outs[c["name"]]
            c["_w"], c["_h"] = estimate(c, sizes)
        if movable:
            names = [c["name"] for c in movable]
            col = _columns(names, links)
            last = max(col.values())
            for c in movable:   # controllers with nothing wired go last, beside the control blocks
                if "_ctrl" in c and not c.get("zone"):
                    col[c["name"]] = last
            by_name = {c["name"]: c for c in movable}
            zone_of = {n: by_name[n].get("zone", "") for n in names}
            # column widths and x, shared by every zone of the page
            width = defaultdict(float)
            for n in names:
                c = by_name[n]
                width[col[n]] = max(width[col[n]], c["_w"], note_w if "note" in c else 0)
            xs, x = {}, x0 + pad
            for k in range(max(col.values()) + 1):
                xs[k] = x
                x += width[k] + col_gap if k in width else 0
            # zones in order of their first column, each stacked under the zones its band overlaps
            members = defaultdict(list)
            for n in names:
                members[zone_of[n]].append(n)
            band = {z: (min(col[n] for n in ns), max(col[n] for n in ns)) for z, ns in members.items()}
            placed = []   # (first col, last col, bottom y)
            row_of = {}
            # reading order: spec["zones"] order, then first appearance; blocks without a zone first
            seen = list(zones_style) + [zone_of[n] for n in names]
            order = sorted(members, key=lambda z: (z != "", seen.index(z) if z in seen else 0))
            for z in order:
                c1, c2 = band[z]
                top = max([b for a1, a2, b in placed if not (a2 < c1 or c2 < a1)] + [y0])
                top += (title_h if z else 0)
                bottom = top
                for k in range(c1, c2 + 1):
                    def bary(n):
                        feeders = [row_of[a] for a, b in links if b == n and a in row_of]
                        return sum(feeders) / len(feeders) if feeders else 1e9
                    y = top
                    for i, n in enumerate(sorted((n for n in members[z] if col[n] == k), key=lambda n: (bary(n), names.index(n)))):
                        c = by_name[n]
                        c["x"], c["y"] = xs[k], y
                        row_of[n] = i
                        y += c["_h"] + (_note_h(c) + 6 if "note" in c else 0) + row_gap
                    bottom = max(bottom, y)
                if z:
                    x1, x2 = xs[c1] - pad, xs[c2] + width[c2] + pad
                    style = zones_style.get(z, {})
                    graphics.append({"type": "group", "text": z, "x": x1, "y": top - title_h + 6, "w": x2 - x1,
                                     "h": bottom - top + title_h - 6, "fill": style.get("fill", ZONE_FILLS[len(placed) % len(ZONE_FILLS)]),
                                     "bold": True, "font_size": 12, "page": page})
                    if style.get("text"):
                        graphics.append({"type": "text", "text": style["text"], "x": x1 + 8, "y": top - title_h + 24,
                                         "w": x2 - x1 - 16, "h": 16, "font_size": 9, "align": "Left", "z": -20, "page": page})
                placed.append((c1, c2, bottom + zone_gap))
        for c in comps:
            if "note" in c and "x" in c:
                graphics.append({"type": "text", "text": c["note"], "x": c["x"], "y": c["y"] + c["_h"] + 4,
                                 "w": max(c["_w"], note_w), "h": _note_h(c), "font_size": 8.5, "align": "Left",
                                 "fill": NOTE_FILL, "z": -10, "page": page})
    for c in ctrls:
        c["_ctrl"]["x"], c["_ctrl"]["y"] = c["x"], c["y"]
    for c in spec.get("components", []):
        for k in ("_ins", "_outs", "_w", "_h", "note", "zone"):
            c.pop(k, None)
    for b in spec.get("snapshot_banks", []):
        b.pop("zone", None)
        b.pop("note", None)
    spec.pop("zones", None)
    spec.pop("layout", None)
    return spec


def _note_h(c: dict) -> float:
    return 6 + 11 * (c["note"].count("\n") + 1)
