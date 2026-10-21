"""Checks that a design offers what a control system (Crestron, AMX, a Lua script...) expects of it:
blocks by code name, reachable with the right Script Access, with the controls it reads and writes.

contract = {
  "via": "qrc",          # default for every block: "qrc" (External or All), "script" (Script or All), "both" (All)
  "components": {
    "Room_Volume": {"controls": ["gain", {"id": "mute", "write": true}]},
    "Mic_Meter":   {"controls": ["meter_1"]},
    "LED_Logic":   {"via": "script", "controls": []}
  }
}
"""

from __future__ import annotations

import difflib
import os

from qsys_mcp import headless

NEEDS = {"qrc": {"External", "All"}, "script": {"Script", "All"}, "both": {"All"}}


def check(path: str, contract: dict) -> dict:
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    wanted = contract.get("components") or {}
    if not isinstance(wanted, dict) or not wanted:
        raise ValueError("contract.components must map code names to what is expected of them")
    default_via = contract.get("via", "qrc")
    inst = headless.pick(for_file=path)
    found = headless.run({"command": "controls", "path": path}, inst)["components"]
    results = []
    for name, spec in wanted.items():
        spec = spec or {}
        via = spec.get("via", default_via)
        if via not in NEEDS:
            raise ValueError(f"{name}: via must be qrc, script or both, not {via!r}")
        r = {"component": name, "via": via}
        block = found.get(name)
        if block is None:
            near = difflib.get_close_matches(name, list(found), n=1, cutoff=0.75)
            r.update(ok=False, problem="no block with this code name" + (f" (did you mean {near[0]}?)" if near else ""))
            results.append(r)
            continue
        access = block["script_access"]
        problems = []
        if access not in NEEDS[via]:
            problems.append(f"Script Access is {access}; {via} needs {' or '.join(sorted(NEEDS[via]))}")
        controls = {c["id"]: c for c in block["controls"]}
        for c in spec.get("controls", []):
            cid, write = (c, False) if isinstance(c, str) else (c["id"], bool(c.get("write")))
            got = controls.get(cid)
            if got is None:
                near = difflib.get_close_matches(cid, list(controls), n=1, cutoff=0.75)
                problems.append(f"no control {cid}" + (f" (did you mean {near[0]}?)" if near else ""))
            elif write and got.get("readonly"):
                problems.append(f"{cid} is read-only")
        r.update(ok=not problems, script_access=access, problems=problems)
        results.append(r)
    exposed = [n for n, b in found.items() if b["script_access"] in ("External", "All") and n not in wanted]
    return {"path": path, "passed": sum(r["ok"] for r in results), "failed": sum(not r["ok"] for r in results),
            "results": results,
            "also_exposed": exposed}   # blocks open to QRC that the contract doesn't mention
