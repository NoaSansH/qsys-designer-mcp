"""Emulating designs and measuring stored values on the engine.

`emulate` opens a design (by default a test copy with every block's Script Access set to All, so
QRC can see everything) and starts Designer's emulator. `calibrate` measures the engine's
normalised position for every stored value, patches the file, then restarts on the patched
file and checks every value came up as stored (which also catches linked controls).
`save_snapshots` stores snapshot values, which only the engine can write, by saving them on the
emulator and keeping the data Designer collects when emulation stops.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from qsys_mcp import designs, headless
from qsys_mcp.app import AppError, Designer
from qsys_mcp.qrc.client import QrcClient, QrcError


def test_copy_path(path: str) -> str:
    d = headless.cache_dir() / "test-copies"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / (Path(path).stem + "__test.qsys"))


def make_test_copy(path: str) -> str:
    """Copy of the design with every block's Script Access = All (the original is untouched)."""
    path = os.path.abspath(path)
    out = test_copy_path(path)
    inst = headless.pick(for_file=path)
    headless.run({"command": "copy", "path": path, "out_path": out, "overwrite": True, "script_access": "All"}, inst)
    return out


async def _wait_active(qrc: QrcClient, design: str, timeout: float = 90) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            st = await qrc.status()
            last = st
            if st.get("State") == "Active" and st.get("DesignName") == design:
                return st
        except QrcError as e:
            last = str(e)
        await asyncio.sleep(1)
    raise AppError(f"Emulation of {design!r} did not become active within {timeout:.0f} s (last QRC status: {last})")


async def emulate(designer: Designer, qrc: QrcClient, path: str, test_copy: bool = True,
                  discard_changes: bool = False) -> dict:
    target = make_test_copy(path) if test_copy else os.path.abspath(path)
    opened = await asyncio.to_thread(designer.open_design, target, discard_changes)
    started = await asyncio.to_thread(designer.emulate)
    notices = []
    if any(designer.is_license_notice(d) for d in started["dialogs"]):
        notices = await asyncio.to_thread(designer.ack_license_notices)   # OK-only notice: note it, carry on
        started["dialogs"] = await asyncio.to_thread(designer.dialogs)
    if started["dialogs"]:
        return {"opened": opened, "emulating": False, "dialogs": started["dialogs"],
                "detail": "Designer is showing a dialog; answer it with dialog_button, then check qrc_status."}
    st = await _wait_active(qrc, Path(target).stem)
    out = {"opened": target, "test_copy": test_copy, "designer": opened["designer"], "emulating": True, "qrc": st}
    if notices:
        out["license_notices"] = notices   # what a real Core would need licensed (emulation runs regardless)
    return out


def _numeric_values(design: dict) -> list[dict]:
    out = []
    for c in design["components"]:
        for ctl, v in c.get("values", {}).items():
            if "value" in v:
                out.append({"component": c["name"], "class": c["class"], "control": ctl, "value": v["value"],
                            "stored_position": v.get("position"), "min": v.get("min"), "max": v.get("max")})
    return out


async def _measure(qrc: QrcClient, values: list[dict]) -> list[dict]:
    """Set each value over QRC and read back what the engine made of it."""
    for v in values:
        await qrc.set(v["component"], v["control"], v["value"])
    await asyncio.sleep(1.0)
    for v in values:
        r = (await qrc.get(v["component"], [v["control"]]))["Controls"][0]
        v["engine_value"], v["engine_position"] = r.get("Value"), r.get("Position")
    return values


async def _read_live(qrc: QrcClient, values: list[dict]) -> list[dict]:
    for v in values:
        r = (await qrc.get(v["component"], [v["control"]]))["Controls"][0]
        v["live_value"], v["live_position"] = r.get("Value"), r.get("Position")
    return values


async def calibrate(designer: Designer, qrc: QrcClient, path: str, discard_changes: bool = False,
                    reopen: bool = True) -> dict:
    path = os.path.abspath(path)
    inst = headless.pick(for_file=path)
    design = designs.read(path)
    values = _numeric_values(design)
    if not values:
        return {"path": path, "detail": "no stored numeric values to calibrate"}

    # 1. measure positions on a test copy
    run = await emulate(designer, qrc, path, test_copy=True, discard_changes=discard_changes)
    if not run.get("emulating"):
        return {"path": path, "stopped_at": "emulate", **run}
    measured = await _measure(qrc, [dict(v) for v in values])
    await asyncio.to_thread(designer.stop)
    await asyncio.to_thread(designer.close_designs, True)   # our own test copy: discard captured values

    # 2. patch the original with the engine's positions, and remember them for future builds
    patch, changed, cache = [], [], []
    for v in measured:
        if v.get("engine_position") is None or abs((v.get("engine_value") or 0) - v["value"]) > 1e-3:
            continue
        pos = float(v["engine_position"])
        patch.append({"component": v["component"], "control": v["control"], "value": v["value"], "position": pos})
        if v.get("min") is not None:
            cache.append({"class": v["class"], "control": v["control"], "min": v["min"], "max": v["max"],
                          "value": v["value"], "position": pos})
        if v["stored_position"] is None or abs(v["stored_position"] - pos) > 1e-4:
            changed.append(f"{v['component']}.{v['control']}={v['value']}: {v['stored_position']} -> {pos:.6g}")
    rejected = [f"{v['component']}.{v['control']}: set {v['value']}, engine kept {v.get('engine_value')}"
                for v in measured if v.get("engine_value") is None or abs(v["engine_value"] - v["value"]) > 1e-3]
    if patch:
        headless.run({"command": "set_values", "path": path, "values": patch}, inst)
        headless.save_positions(inst, cache)

    # 3. restart on the patched file and confirm every stored value comes up as stored
    run2 = await emulate(designer, qrc, path, test_copy=True, discard_changes=True)
    live = await _read_live(qrc, [dict(v) for v in values]) if run2.get("emulating") else []
    await asyncio.to_thread(designer.stop)
    await asyncio.to_thread(designer.close_designs, True)
    mismatched = [f"{v['component']}.{v['control']}: stored {v['value']}, came up as {v['live_value']}"
                  for v in live if v.get("live_value") is None or abs(v["live_value"] - v["value"]) > 1e-3]
    if reopen:
        await asyncio.to_thread(designer.open_design, path, False)
    return {
        "path": path, "designer": inst.version, "values": len(values), "patched": len(patch),
        "positions_changed": changed, "engine_rejected": rejected,
        "verified_after_restart": len(live) - len(mismatched), "mismatched_after_restart": mismatched,
        "hint": ("A value that is right when set live but wrong after a restart usually has a linked control "
                 "(e.g. Flex preamp gain and sensitivity): store the linked control too.") if mismatched else None,
    }


# ---- snapshot values ----------------------------------------------------------------------

def _qrc_value(v):
    return int(v) if isinstance(v, bool) else v   # booleans as 1/0, like the buttons themselves


def _matches(expected, live: dict) -> bool:
    """Does a control read back over QRC hold the expected spec value?"""
    if isinstance(expected, str):
        return live.get("String") == expected
    got = live.get("Value")
    if got is None:
        return False
    if isinstance(expected, bool):
        return bool(got) == expected
    return abs(float(got) - float(expected)) <= 1e-3 * max(1.0, abs(float(expected)))


async def _put_back(qrc: QrcClient, start: dict, texts: set) -> None:
    """Sets controls back to the values they started the run with."""
    for (comp, ctl), r in start.items():
        await qrc.set(comp, ctl, r.get("String", "") if (comp, ctl) in texts else r.get("Value"))


async def _press(qrc: QrcClient, bank: dict, button: str, n: int) -> None:
    """The bank controller's load_N or save_N, or the bank's own QRC method if the block isn't reachable."""
    try:
        await qrc.set(bank["controller"], f"{button}_{n}", 1)
    except QrcError:
        await qrc.call("Snapshot.Save" if button == "save" else "Snapshot.Load", {"Name": bank["bank"], "Bank": n})


async def _save_on_engine(qrc: QrcClient, plan: list[dict]) -> list[dict]:
    """Saves every planned snapshot on the running design, checks each by loading it back, and leaves the
    controls as the run found them (Designer records a run's values when it stops). Each snapshot starts
    from the design's own values, so a control a snapshot doesn't give keeps the design's value."""
    touched = sorted({(c, k) for b in plan for sets in b["snapshots"].values() for c, k, _ in sets})
    texts = {(c, k) for b in plan for sets in b["snapshots"].values() for c, k, v in sets if isinstance(v, str)}
    start = {ck: (await qrc.get(ck[0], [ck[1]]))["Controls"][0] for ck in touched}
    report = []
    for b in plan:
        try:
            ramp = float((await qrc.get(b["controller"], ["ramp_time"]))["Controls"][0].get("Value") or 0)
        except QrcError:
            ramp = 0.0
        for n, sets in sorted(b["snapshots"].items()):
            await _put_back(qrc, start, texts)
            for comp, ctl, v in sets:
                await qrc.set(comp, ctl, _qrc_value(v))
            await asyncio.sleep(0.5)
            await _press(qrc, b, "save", n)
            await asyncio.sleep(0.5)
        mismatched = []
        for n, sets in sorted(b["snapshots"].items()):
            await _put_back(qrc, start, texts)   # so a snapshot that didn't save can't pass
            await asyncio.sleep(0.3)
            await _press(qrc, b, "load", n)
            await asyncio.sleep(min(ramp, 10) + 0.8)
            for comp, ctl, v in sets:
                live = (await qrc.get(comp, [ctl]))["Controls"][0]
                if not _matches(v, live):
                    mismatched.append(f"snapshot {n}: {comp}.{ctl} should be {v!r}, loaded as "
                                      f"{live.get('String') if isinstance(v, str) else live.get('Value')!r}")
        report.append({"bank": b["bank"], "saved": sorted(b["snapshots"]), "mismatched_on_load": mismatched})
    await _put_back(qrc, start, texts)
    await asyncio.sleep(0.5)
    return report


async def save_snapshots(designer: Designer, qrc: QrcClient, path: str, spec_or_values,
                         discard_changes: bool = False) -> dict:
    path = os.path.abspath(path)
    inst = headless.pick(for_file=path)
    design = designs.read(path)
    plan, problems = designs.snapshot_plan(design, designs.snapshot_values(spec_or_values))
    if problems:
        raise designs.SpecError("snapshot values don't match the design:\n- " + "\n- ".join(problems))
    if not plan:
        return {"path": path, "detail": "no snapshot values to save"}
    access = {c["name"]: c["script_access"] for c in design["components"]}
    access.update({b["controller"]["name"]: b["controller"]["script_access"]
                   for b in design["snapshots"].get("banks", []) if b.get("controller")})

    # 1. save every snapshot on a test copy (every block visible to QRC), then keep what Designer collects
    run = await emulate(designer, qrc, path, test_copy=True, discard_changes=discard_changes)
    if not run.get("emulating"):
        return {"path": path, "stopped_at": "emulate", **run}
    work = run["opened"]
    banks = await _save_on_engine(qrc, plan)
    stopped = await asyncio.to_thread(designer.stop)
    if stopped["dialogs"]:
        return {"path": path, "stopped_at": "stop", "banks": banks, **stopped,
                "detail": "Designer is showing a dialog after stopping; the original file is unchanged."}
    saved = await asyncio.to_thread(designer.save, work, 10)
    if not saved["saved"]:
        return {"path": path, "stopped_at": "save", "banks": banks, "work_copy": work, **saved,
                "detail": "The test copy could not be saved in Designer; the original file is unchanged."}
    await asyncio.to_thread(designer.close_designs, False)

    # 2. the saved copy, with each block's own Script Access back, replaces the original
    headless.run({"command": "copy", "path": work, "out_path": path, "overwrite": True, "script_access_map": access}, inst)
    after = designs.read(path)
    stored = {b["name"]: b.get("stored", []) for b in (after.get("snapshots") or {}).get("banks", [])}
    missing = [f"{b['bank']} {n}" for b in plan for n in b["snapshots"] if n not in stored.get(b["bank"], [])]
    return {"path": path, "designer": inst.version, "banks": banks, "stored": stored, "missing_in_file": missing,
            "hint": ("Snapshots saved on the engine but missing from the file: Designer didn't collect their data "
                     "when emulation stopped.") if missing else None}
