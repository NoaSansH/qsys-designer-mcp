"""Emulating designs and measuring stored values on the engine.

`emulate` opens a design (by default a test copy with every block's Script Access set to All, so
QRC can see everything) and starts Designer's emulator. `calibrate` measures the engine's
normalised position for every stored value, patches the file, then restarts on the patched
file and checks every value came up as stored (which also catches linked controls).
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
    # The license notice (OK only) is noted and acknowledged; "Core Status: Compiling Design..." is
    # progress, not a question, so wait for it to go away. They come in either order.
    notices = []
    deadline = time.time() + 300
    while started["dialogs"] and time.time() < deadline:
        if any(designer.is_license_notice(d) for d in started["dialogs"]):
            notices += await asyncio.to_thread(designer.ack_license_notices)
        elif all(d.get("title") == "Core Status" for d in started["dialogs"]):
            await asyncio.sleep(1)
        else:
            break
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


async def record_snapshots(designer: Designer, qrc: QrcClient, path: str, snapshots: dict,
                           discard_changes: bool = False) -> dict:
    """Fill snapshot banks with content. Snapshot data is written by the engine, not stored as
    control values, so each snapshot is saved for real: a test copy is emulated, the controls are
    set over QRC, the bank's Snapshot Controller saves them, the copy is saved in Designer and the
    banks' saved data is copied into the original file.

    snapshots: {"BANK": {"controller": "CODE_NAME" (default: the bank name),
                         "snapshots": {"1": [{"component": "MIX", "control": "input_1_output_1_mute", "value": 1}], ...}}}"""
    path = os.path.abspath(path)
    inst = headless.pick(for_file=path)
    run = await emulate(designer, qrc, path, test_copy=True, discard_changes=discard_changes)
    if not run.get("emulating"):
        return {"path": path, "stopped_at": "emulate", **run}
    copy_path = run["opened"]
    saved, problems = {}, []
    for bank, spec in snapshots.items():
        controller = spec.get("controller", bank)
        saved[bank] = []
        for number, values in sorted(spec["snapshots"].items(), key=lambda kv: int(kv[0])):
            for v in values:
                await qrc.set(v["component"], v["control"], v["value"])
            await asyncio.sleep(0.5)
            for v in values:
                got = (await qrc.get(v["component"], [v["control"]]))["Controls"][0].get("Value")
                want = float(v["value"]) if not isinstance(v["value"], str) else v["value"]
                if isinstance(want, float) and (got is None or abs(float(got) - want) > 1e-3):
                    problems.append(f"{bank} {number}: {v['component']}.{v['control']} set {v['value']}, engine has {got}")
            await qrc.set(controller, f"save_{number}", 1)
            await asyncio.sleep(1.0)
            saved[bank].append(int(number))
    await asyncio.to_thread(designer.stop)
    await asyncio.to_thread(designer.menu, "File->Save")
    await asyncio.sleep(2)
    await asyncio.to_thread(designer.close_designs, True)   # our own test copy, saved just above
    res = headless.run({"command": "copy_snapshots", "from": copy_path, "path": path, "banks": list(snapshots)}, inst)
    missing = {b: sorted(set(n) - set(res["copied"].get(b, []))) for b, n in saved.items()}
    missing = {b: n for b, n in missing.items() if n}
    return {"path": path, "saved_in_emulation": saved, "copied_into_design": res["copied"],
            "not_copied": missing, "problems": problems}
