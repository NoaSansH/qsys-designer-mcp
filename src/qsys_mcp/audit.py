"""Automatic design review: the mistakes that make a room unusable, found from the saved design.

Signal paths are followed over wires and signal names, through each block, in a given state: a mixer
crosspoint that is muted or at -100 dB, or a muted gain block, stops the signal. The state is the
design's stored values, then each snapshot's contents when given (from snapshot_contents), so "the mic
never reaches the speakers" is checked in every room mode, not only as wired.

Gain structure: a nominal level (-20 dBFS unless roles["levels"] says otherwise for a block) leaves every
source and is carried through the blocks' gains: gain blocks, mixer input, crosspoint and output gains,
limiter thresholds; sources meeting in a mixer add up. A level above 0 dBFS anywhere is clipping, above
-6 dBFS at an output leaves little headroom, below -45 dBFS at an output is lost in the noise.

Roles say what is what. Without them they're inferred: speakers = Flex/Line outputs and amplifiers,
microphones = Mic/Line inputs, PC links = each USB bridge's In/Out pair.
  roles = {"mics": ["MIC_RX.out.1"], "speakers": ["SPK_OUT"], "pc_links": [["USB_IN", "USB_OUT"]],
           "aec_reference": "REF_TX.in.1", "volume": "ROOM_VOL"}
"""

from __future__ import annotations

import re
import math
from collections import defaultdict, deque

from qsys_mcp.designs import ENDPOINT_RE, NON_PASSING

SPEAKER_CLASSES = re.compile(r"^(io_card_(flex|line)_out|amplifier|io_card_.*_out_)")
MIC_CLASSES = re.compile(r"^io_card_mic_line_in")
OFF_DB = -99.5
NOMINAL = -20.0          # dBFS a source is assumed to deliver
CLIP, HEADROOM, NOISE = 0.0, -6.0, -45.0
NOT_AN_OUTPUT = re.compile(r"meter|level|snapshot|controller", re.I)


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
        self.gain = {}                     # (from pin, to pin) -> dB through a block
        self.ceiling = {}                  # output pin -> the most it can carry (a limiter's threshold)
        for a, b in design["wires"]:
            self.edges[a].append(b)
        for c in design["components"]:
            ins = [p for p in c["pins"] if p["dir"] == "in" and not p.get("control")]
            outs = [p for p in c["pins"] if p["dir"] == "out" and not p.get("control")]
            for i in ins:
                if (c["class"], i["name"]) in NON_PASSING:
                    continue
                for o in outs:
                    if self._passes(c, i, o):
                        a, b = f"{c['name']}.in.{i['index']}", f"{c['name']}.out.{o['index']}"
                        self.edges[a].append(b)
                        self.gain[(a, b)] = self._gain(c, i, o)
                        if c["class"].startswith("limiter"):
                            t = _db(self.value(c, "threshold_level"))
                            self.ceiling[b] = t if t is not None else 0.0

    def _gain(self, c: dict, i: dict, o: dict) -> float:
        cls = c["class"]

        def db(control):
            v = _db(self.value(c, control))
            return v if v is not None else 0.0
        if cls == "gain":
            return db("gain")
        if cls == "mixer":
            ni, _ = _num(i["name"])
            no, _ = _num(o["name"])
            if ni is None or no is None:
                return 0.0
            return db(f"input_{ni}_gain") + db(f"input_{ni}_output_{no}_gain") + db(f"output_{no}_gain")
        return 0.0

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
            if ni is not None and no is not None and not si and ni != no:   # multi-channel: channel to channel
                return False
            return g is None or g > OFF_DB
        if cls.startswith(("equalizer", "limiter", "compressor", "filter", "delay")):
            ni, si = _num(i["name"])
            no, so = _num(o["name"])
            return (si == so if si or so else ni is None or no is None or ni == no)
        return True

    def levels(self, sources: dict[str, float]) -> dict[str, float]:
        """Nominal level at every pin reached, from each source pin's level: summed in power where
        sources meet, each source following its loudest path; limiters cap what they pass."""
        total = defaultdict(float)
        for start, level in sources.items():
            best = {start: level}
            todo = [start]
            for _ in range(20000):
                if not todo:
                    break
                p = todo.pop()
                for q in self.edges.get(p, []):
                    lv = best[p] + self.gain.get((p, q), 0.0)
                    if q in self.ceiling:
                        lv = min(lv, self.ceiling[q])
                    if q not in best or lv > best[q] + 0.01:
                        best[q] = lv
                        todo.append(q)
            for p, lv in best.items():
                total[p] += 10 ** (lv / 10)
        return {p: 10 * math.log10(v) for p, v in total.items() if v > 0}

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


def _merge_states(findings: list[dict]) -> list[dict]:
    """The same finding in several states, once, with the states it shows in."""
    out, seen = [], {}
    for f in findings:
        key = (f["severity"], f["rule"], f["detail"])
        if key in seen and "state" in f:
            first = seen[key]
            first["states"] = first.get("states", [first.pop("state")] if "state" in first else []) + [f["state"]]
            continue
        seen[key] = f
        out.append(f)
    return out


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


def _audio(p: dict) -> bool:
    return not p.get("control") and not p.get("domain")


def _sources(design: dict, levels: dict) -> dict[str, float]:
    """Output pins of the blocks that bring signal in (no audio input), at their nominal level."""
    out = {}
    for c in design["components"]:
        ins = [p for p in c["pins"] if p["dir"] == "in" and _audio(p)]
        outs = [p for p in c["pins"] if p["dir"] == "out" and _audio(p)]
        if outs and not ins:
            for p in outs:
                key = f"{c['name']}.out.{p['index']}"
                out[key] = float(levels.get(key, levels.get(c["name"], NOMINAL)))
    return out


def _outputs(design: dict) -> list[str]:
    """Input pins of the blocks that take signal out (no audio output): speakers, PC sends, network TX."""
    out = []
    for c in design["components"]:
        if NOT_AN_OUTPUT.search(c["class"]):
            continue
        ins = [p for p in c["pins"] if p["dir"] == "in" and _audio(p)]
        outs = [p for p in c["pins"] if p["dir"] == "out" and _audio(p)]
        if ins and not outs:
            out += [f"{c['name']}.in.{p['index']}" for p in ins]
    return out


def gain_structure(design: dict, state: dict | None = None, levels: dict | None = None) -> dict:
    """{"outputs": {pin: dBFS}, "clipping": {pin: dBFS}} at nominal source levels."""
    g = Graph(design, state)
    lv = g.levels(_sources(design, levels or {}))
    outputs = {p: round(lv[p], 1) for p in _outputs(design) if p in lv}
    clipping = {p: round(v, 1) for p, v in lv.items() if v > CLIP + 0.05}
    return {"outputs": outputs, "clipping": clipping}


def audit(design: dict, roles: dict | None = None, snapshot_values: dict | None = None) -> dict:
    roles = {**infer_roles(design), **(roles or {})}
    states = {"stored values": {}}
    for bank, snaps in (snapshot_values or {}).items():
        for n, vals in snaps.items():
            states[f"{bank} {n}"] = vals
    findings = []
    output_levels = {}

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
        gs = gain_structure(design, state, roles.get("levels"))
        for p, v in gs["clipping"].items():
            add("error", "clipping at nominal level", f"{p}: {v:+.1f} dBFS", state_name)
        for p, v in gs["outputs"].items():
            if p in gs["clipping"]:
                continue
            if v > HEADROOM:
                add("warning", "little headroom at an output", f"{p}: {v:+.1f} dBFS at nominal level", state_name)
            elif v < NOISE:
                add("warning", "very low level at an output", f"{p}: {v:+.1f} dBFS at nominal level", state_name)
        if gs["outputs"]:
            output_levels[state_name] = gs["outputs"]
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
    findings = _merge_states(findings)
    return {"roles": roles, "states_checked": list(states), "output_levels": output_levels,
            "errors": sum(f["severity"] == "error" for f in findings),
            "warnings": sum(f["severity"] == "warning" for f in findings), "findings": findings}
