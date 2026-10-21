"""Automatic design review: the mistakes that make a room unusable, found from the saved design.

Signal paths are followed over wires and signal names, through each block, in a given state: a mixer
crosspoint that is muted or at -100 dB, or a muted gain block, stops the signal. The state is the
design's stored values, then each snapshot's contents when given (from snapshot_contents), so "the mic
never reaches the speakers" is checked in every room mode, not only as wired.

Roles say what is what. Without them they're inferred: speakers = Flex/Line outputs and amplifiers,
microphones = Mic/Line inputs, PC links = each USB bridge's In/Out pair.
  roles = {"mics": ["MIC_RX.out.1"], "speakers": ["SPK_OUT"], "pc_links": [["USB_IN", "USB_OUT"]],
           "aec_reference": "REF_TX.in.1", "volume": "ROOM_VOL"}
"""

from __future__ import annotations

import re
from collections import defaultdict, deque

from qsys_mcp.designs import ENDPOINT_RE, NON_PASSING

SPEAKER_CLASSES = re.compile(r"^(io_card_(flex|line)_out|amplifier|io_card_.*_out_)")
MIC_CLASSES = re.compile(r"^io_card_mic_line_in")
OFF_DB = -99.5


def _muted(v) -> bool:
    if v is None:
        return False
    if isinstance(v, dict):
        v = v.get("string", v.get("value"))
    s = str(v).strip().lower()
    return s in ("1", "1.0", "true", "muted", "on")


def _db(v):
    if isinstance(v, dict):
        v = v.get("value", v.get("string"))
    try:
        return float(str(v).lower().replace("db", "").strip())
    except (TypeError, ValueError):
        return None


def _num(name: str):
    m = re.search(r"(\d+)\s*(left|right)?$", name.strip(), re.I)
    return (int(m.group(1)), (m.group(2) or "").lower()) if m else (None, "")


class Graph:
    def __init__(self, design: dict, state: dict | None = None):
        """state: {"BLOCK.control": value} over the stored values (a snapshot's contents)."""
        self.design = design
        self.by_name = {c["name"]: c for c in design["components"]}
        self.state = state or {}
        self.edges = defaultdict(list)
        for a, b in design["wires"]:
            self.edges[a].append(b)
        for c in design["components"]:
            ins = [p for p in c["pins"] if p["dir"] == "in" and not p.get("control")]
            outs = [p for p in c["pins"] if p["dir"] == "out"]
            for i in ins:
                if (c["class"], i["name"]) in NON_PASSING:
                    continue
                for o in outs:
                    if self._passes(c, i, o):
                        self.edges[f"{c['name']}.in.{i['index']}"].append(f"{c['name']}.out.{o['index']}")

    def value(self, c: dict, control: str):
        key = f"{c['name']}.{control}"
        if key in self.state:
            return self.state[key]
        return c.get("values", {}).get(control)

    def _passes(self, c: dict, i: dict, o: dict) -> bool:
        cls = c["class"]
        if cls == "mixer":
            ni, _ = _num(i["name"])
            no, _ = _num(o["name"])
            if ni is None or no is None:
                return True
            if _muted(self.value(c, f"input_{ni}_mute")) or _muted(self.value(c, f"output_{no}_mute")):
                return False
            if _muted(self.value(c, f"input_{ni}_output_{no}_mute")):
                return False
            g = _db(self.value(c, f"input_{ni}_output_{no}_gain"))
            return g is None or g > OFF_DB
        if cls == "gain":
            if _muted(self.value(c, "mute")):
                return False
            g = _db(self.value(c, "gain"))
            ni, si = _num(i["name"])
            no, so = _num(o["name"])
            if si and so and si != so:          # stereo: left to left, right to right
                return False
            return g is None or g > OFF_DB
        return True

    def reach(self, starts: list[str]) -> set[str]:
        seen, q = set(), deque(starts)
        while q:
            p = q.popleft()
            if p in seen:
                continue
            seen.add(p)
            q.extend(self.edges.get(p, []))
        return seen

    def pins(self, ref: str, direction: str | None = None) -> list[str]:
        m = ENDPOINT_RE.match(ref)
        name = m["name"] if m else ref
        c = self.by_name.get(name)
        if c is None:
            return []
        if m:
            return [f"{name}.{p['dir']}.{p['index']}" for p in c["pins"]
                    if p["dir"] == m["dir"] and (str(p["index"]) == m["pin"] or p["name"].lower() == m["pin"].lower())]
        return [f"{name}.{p['dir']}.{p['index']}" for p in c["pins"]
                if (direction is None or p["dir"] == direction) and not p.get("control")]


def infer_roles(design: dict) -> dict:
    comps = design["components"]
    speakers = [c["name"] for c in comps if SPEAKER_CLASSES.match(c["class"])]
    mics = [c["name"] for c in comps if MIC_CLASSES.match(c["class"])]
    links = []
    rx = {c["properties"].get("bridge_name"): c["name"] for c in comps if c["class"] == "usb_receiver"}
    for c in comps:
        if c["class"] == "usb_transmitter" and c["properties"].get("bridge_name") in rx:
            links.append([rx[c["properties"]["bridge_name"]], c["name"]])
    return {"mics": mics, "speakers": speakers, "pc_links": links}


def audit(design: dict, roles: dict | None = None, snapshot_values: dict | None = None) -> dict:
    roles = {**infer_roles(design), **(roles or {})}
    states = {"stored values": {}}
    for bank, snaps in (snapshot_values or {}).items():
        for n, vals in snaps.items():
            states[f"{bank} {n}"] = vals
    findings = []

    def add(severity, rule, detail, state=None):
        findings.append({"severity": severity, "rule": rule, "detail": detail, **({"state": state} if state else {})})

    for state_name, state in states.items():
        g = Graph(design, state)
        spk_in = [p for s in roles["speakers"] for p in g.pins(s, "in")]
        for mic in roles["mics"]:
            reached = g.reach(g.pins(mic, "out"))
            hit = sorted(p for p in spk_in if p in reached)
            if hit:
                add("error", "microphone reaches the loudspeakers (feedback)", f"{mic} -> {', '.join(hit[:4])}", state_name)
        for rx, tx in roles["pc_links"]:
            reached = g.reach(g.pins(rx, "out"))
            hit = [p for p in g.pins(tx, "in") if p in reached]
            if hit:
                add("error", "a PC gets its own audio back (echo or loop)", f"{rx} -> {tx}", state_name)
        if roles.get("aec_reference") and roles.get("volume") and state_name == "stored values":
            vol_out = g.pins(roles["volume"], "out")
            ref = g.pins(roles["aec_reference"], "in")
            reached = Graph(design, {}).reach(vol_out)   # wiring only: is the reference after the volume?
            if not any(p in reached for p in ref):
                add("warning", "AEC reference is not taken after the room volume",
                    f"{roles['aec_reference']} isn't fed from {roles['volume']}: far-end level changes won't follow")

    # static hygiene, once
    wired = {p for w in design["wires"] for p in w}
    for c in design["components"]:
        sig = [p for p in c["pins"] if not p.get("control")]
        if sig and not any(f"{c['name']}.{p['dir']}.{p['index']}" in wired for p in sig):
            add("warning", "block with no connection", c["name"])
        v = c.get("values", {})
        if c["class"] == "limiter_peak" and (_db(v.get("threshold_level")) or 0) > 0:
            add("warning", "limiter threshold above 0 dBFS", f"{c['name']}: {_db(v.get('threshold_level'))} dB")
        for k, val in v.items():
            d = _db(val) if isinstance(val, dict) and "value" in val else None
            if d is not None and k.endswith("gain") and d > 12 and c["class"] in ("gain", "mixer"):
                add("info", "high stored gain", f"{c['name']}.{k} = {d:+g} dB")
    exposed = [c["name"] for c in design["components"] if c.get("script_access") in ("External", "All")]
    if exposed:
        add("info", "blocks open to QRC (check each is needed)", ", ".join(exposed))
    order = {"error": 0, "warning": 1, "info": 2}
    findings.sort(key=lambda f: order[f["severity"]])
    return {"roles": roles, "states_checked": list(states), "errors": sum(f["severity"] == "error" for f in findings),
            "warnings": sum(f["severity"] == "warning" for f in findings), "findings": findings}
