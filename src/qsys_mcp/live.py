"""A running design (a real Core, or the emulator) against its file, over QRC.

compare: does the Core run this design, and do its controls hold the file's stored values?
pull_snapshots: what each snapshot of a bank holds on the Core (camera positions saved on site...),
read by recalling each one in turn and putting the controls back as they were afterwards. Only blocks
whose Script Access lets QRC in (External or All) can be read this way.
"""

from __future__ import annotations

import asyncio
import os

from qsys_mcp.qrc.client import QrcClient, QrcError

TOLERANCE = 0.01


def _stored(v):
    """A stored value from read_design: its number, or its text."""
    if isinstance(v, dict):
        return v.get("value", v.get("string"))
    return v


def _same(stored, live: dict) -> bool:
    if isinstance(stored, str):
        return stored == live.get("String") or (live.get("Value") is not None and _num_eq(stored, live.get("Value")))
    return _num_eq(stored, live.get("Value"))


def _num_eq(a, b) -> bool:
    try:
        a, b = float(a), float(b)
    except (TypeError, ValueError):
        return str(a) == str(b)
    return abs(a - b) <= TOLERANCE * max(1.0, abs(a))


def _live_value(c: dict, kind: str | None = None):
    if (kind or c.get("Type")) == "Text" or "Value" not in c:
        return c.get("String")
    v = c.get("Value")
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return int(v) if float(v).is_integer() else round(float(v), 6)
    return c.get("String")


async def _controls(qrc: QrcClient, component: str) -> dict | None:
    try:
        r = await qrc.controls(component)
    except QrcError:
        return None
    return {c["Name"].replace(".", "_"): c for c in r.get("Controls", [])} | {c["Name"]: c for c in r.get("Controls", [])}


async def compare(qrc: QrcClient, design: dict, components: list[str] | None = None) -> dict:
    status = await qrc.status()
    stem = os.path.splitext(os.path.basename(design["path"]))[0]
    differences, hidden, checked = [], [], 0
    for c in design["components"]:
        if components and c["name"] not in components:
            continue
        stored = {k: _stored(v) for k, v in c.get("values", {}).items() if k != "code" and _stored(v) is not None}
        if not stored:
            continue
        live = await _controls(qrc, c["name"])
        if live is None:
            hidden.append(c["name"])
            continue
        for k, v in stored.items():
            checked += 1
            got = live.get(k)
            if got is None:
                differences.append({"component": c["name"], "control": k, "file": v, "core": "(no such control)"})
            elif not _same(v, got):
                differences.append({"component": c["name"], "control": k, "file": v, "core": _live_value(got),
                                    "core_string": got.get("String")})
    running = status.get("DesignName", "")
    return {"core": qrc.host, "running_design": running, "design_code": status.get("DesignCode"),
            "same_design_name": running in (stem, stem + "__test"), "platform": status.get("Platform"),
            "checked": checked, "differences": differences, "not_visible_over_qrc": hidden,
            "same": not differences and not hidden}


async def pull_snapshots(qrc: QrcClient, design: dict, banks: list[str] | None = None,
                         numbers: list[int] | None = None, settle: float = 1.0) -> dict:
    """{"BANK": {"snapshots": {"1": [{"component", "control", "value"}]}, "controller": ..., "empty": [...],
    "not_visible": [...]}} from the Core; the state of the bank's controls is put back afterwards."""
    out = {}
    for b in design.get("snapshot_banks", []):
        if banks and b["name"] not in banks:
            continue
        if not b.get("controller"):
            raise ValueError(f"bank {b['name']} has no Snapshot Controller on the schematic: it can't be recalled over QRC")
        members, hidden, kinds = [], [], {}
        for m in b["controls"]:
            live = await _controls(qrc, m["component"])
            if live is None:
                hidden.append(m["component"])
                continue
            names = [n for n in live if "." not in n] if m.get("all") else m["controls"]
            members += [(m["component"], n) for n in names]
            kinds.update({(m["component"], n): (live.get(n) or {}).get("Type") for n in names})
        before = {}
        for comp in sorted({c for c, _ in members}):
            for r in (await qrc.get(comp, [n for c, n in members if c == comp]))["Controls"]:
                before[(comp, r["Name"])] = r
        result = {"controller": b["controller"], "snapshots": {}, "empty": [], "not_visible": hidden}
        try:
            for n in numbers or range(1, b["count"] + 1):
                await qrc.set(b["controller"], f"load_{n}", 1)
                await asyncio.sleep(settle)
                match = (await qrc.get(b["controller"], [f"match_{n}"]))["Controls"][0].get("Value")
                if not match:
                    result["empty"].append(n)
                    continue
                values = []
                for comp in sorted({c for c, _ in members}):
                    for r in (await qrc.get(comp, [x for c, x in members if c == comp]))["Controls"]:
                        values.append({"component": comp, "control": r["Name"], "value": _live_value(r, kinds.get((comp, r["Name"])))})
                result["snapshots"][str(n)] = values
        finally:
            for (comp, name), r in before.items():      # the room as it was
                await qrc.set(comp, name, _live_value(r, kinds.get((comp, name))))
        out[b["name"]] = result
    return out
