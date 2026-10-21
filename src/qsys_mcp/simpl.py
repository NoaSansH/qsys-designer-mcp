"""What a Crestron SIMPL program expects of a Q-SYS design, read from the program itself.

A SIMPL program (.smw, a text file) holds every module instance with its parameters (P1..Pn) and the
signals on its inputs and outputs (I1.., O1..). Each module's .usp declares what those positions mean.
From the Q-SYS modules (QscQsys library and alike) this builds a contract for check_contract: the
blocks the program addresses by code name and the controls it reads or writes.

Modules are described in MODULES below; others with a "ComponentName" parameter are still listed (the
block must exist and be reachable), and extra descriptions can be passed in.
"""

from __future__ import annotations

import re
from pathlib import Path

RESERVED = re.compile(r"^//__\w+_reserved__$")

# module (lower-case .usp name without extension) -> how it uses its Q-SYS block
#   controls: always used ("{Param}" is replaced by that parameter's value)
#   inputs / outputs: used only when that input or output is wired in the program
#   control_param: the parameter holding a control name (generic modules)
#   writes: what the module writes (the rest is read)
CAMERA_INPUTS = {
    "TiltUp": "tilt_up", "TiltDown": "tilt_down", "PanLeft": "pan_left", "PanRight": "pan_right",
    "PanLeftUp": "pan_left_tilt_up", "PanLeftDown": "pan_left_tilt_down", "PanRightUp": "pan_right_tilt_up",
    "PanRightDown": "pan_right_tilt_down", "ZoomIn": "zoom_in", "ZoomOut": "zoom_out", "AutoFocus": "focus_auto",
    "FocusNear": "focus_near", "FocusFar": "focus_far", "RecallHome": "preset_home_load", "SaveHome": "preset_home_save_trigger",
    "PrivacyOn": "toggle_privacy", "PrivacyOff": "toggle_privacy", "PrivacyToggle": "toggle_privacy",
    "AutoFrameOn": "autoframe_enable", "AutoFrameOff": "autoframe_enable", "AutoFrameToggle": "autoframe_enable",
    "Brightness": "img_brightness", "Saturation": "img_saturation", "Sharpness": "img_sharpness", "Contrast": "img_contrast",
    "ExposureMode": "exp_mode", "Iris": "exp_iris", "Shutter": "exp_shutter", "Gain": "exp_gain",
    "AutoWhiteBalanceMode": "wb_awb_mode", "AutoWhiteBalanceSensitivity": "wb_awb_sensitivity", "WhiteBalanceHue": "wb_hue",
    "WhiteBalanceRedGain": "wb_red_gain", "WhiteBalanceBlueGain": "wb_blue_gain"}
MODULES = {
    "qsys fader": {"controls": ["gain", "mute"], "writes": ["gain", "mute"]},
    "qsys meter": {"controls": ["meter_{Index}"]},
    "qsys snapshot controller": {"controls": ["load_1"], "writes": ["load_1"], "inputs": {"Save": "save_1"}},
    "qsys camera": {"inputs": CAMERA_INPUTS, "outputs": {
        "PrivacyIsOn": "toggle_privacy", "AutoFocusIsOn": "focus_auto", "AutoFrameIsOn": "autoframe_enable",
        **{k + "Value": v for k, v in CAMERA_INPUTS.items() if k[0].isupper() and v.split("_")[0] in ("img", "exp", "wb")}}},
    "qsys generic boolean component control": {"control_param": "ComponentControlName"},
    "qsys generic integer component control": {"control_param": "ComponentControlName"},
    "qsys generic string component control": {"control_param": "ComponentControlName"},
}
SIGNAL_KINDS = (("digital_input", "analog_input", "string_input", "buffer_input"),
                ("digital_output", "analog_output", "string_output"))


def _blocks(text: str) -> list[dict]:
    out = []
    for b in re.findall(r"\[\r?\n(.*?)\r?\n\]", text, re.S):
        kv = {}
        for line in b.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kv[k] = v
        out.append(kv)
    return out


def _decls(text: str, kinds: tuple[str, ...]) -> list[str]:
    """Names declared with any of these keywords, in source order (_skip_ kept: it takes a position)."""
    code = re.sub(r"//[^\n]*|/\*.*?\*/", "", text, flags=re.S)
    names = []
    for m in re.finditer(r"^\s*(" + "|".join(kinds) + r")\s+(.*?);", code, re.I | re.M | re.S):
        for part in m.group(2).split(","):
            name = re.sub(r"\[.*?\]", "", part).strip()
            if name:
                names.append(name)
    return names


def parse_usp(path: str | Path) -> dict:
    text = Path(path).read_text(encoding="latin-1")
    params = _decls(text, ("string_parameter", "integer_parameter", "signed_integer_parameter", "long_integer_parameter"))
    defaults = {m.group(1): m.group(2).strip().strip('"').rstrip("d")
                for m in re.finditer(r"#BEGIN_PARAMETER_PROPERTIES\s+(\w+).*?propDefaultValue\s*=\s*([^;]+);", text, re.S | re.I)}
    # SIMPL numbers inputs digital first, then analog, then serial; same for outputs
    ins = [n for kind in SIGNAL_KINDS[0] for n in _decls(text, (kind,))]
    outs = [n for kind in SIGNAL_KINDS[1] for n in _decls(text, (kind,))]
    return {"params": params, "defaults": defaults, "inputs": ins, "outputs": outs}


def read_program(smw: str | Path, usp_dirs: list[str | Path] | None = None) -> list[dict]:
    """Every module instance: module name, comment, parameters by name, wired inputs and outputs."""
    smw = Path(smw)
    blocks = _blocks(smw.read_text(encoding="latin-1"))
    signals = {b["H"]: b.get("Nm", "") for b in blocks if b.get("ObjTp") == "Sg" and "H" in b}
    dirs = [Path(d) for d in (usp_dirs or [])] + [smw.parent]
    cache, out = {}, []
    for b in blocks:
        name = b.get("Nm", "")
        if b.get("ObjTp") != "Sm" or not name.lower().endswith(".usp"):
            continue
        if name not in cache:
            usp = next((d / name for d in dirs if (d / name).is_file()), None)
            cache[name] = parse_usp(usp) if usp else None
        decl = cache[name]
        inst = {"module": name[:-4], "comment": b.get("Cmn1", ""), "found_usp": decl is not None}
        if decl:
            params = dict(decl["defaults"])
            for i, pname in enumerate(decl["params"], start=2):   # P1 is SIMPL's own; P2.. follow the declarations
                v = b.get(f"P{i}")
                if pname.lower() != "_skip_" and v not in (None, ""):
                    params[pname] = v.rstrip("d") if re.fullmatch(r"-?\d+d", v) else v
            inst["params"] = params

            def wired(prefix, names):
                used = {}
                for k, v in b.items():
                    if re.fullmatch(prefix + r"\d+", k) and not RESERVED.match(signals.get(v, "")):
                        idx = int(k[len(prefix):]) - 1
                        if 0 <= idx < len(names) and names[idx].lower() != "_skip_":
                            used[names[idx]] = signals.get(v, v)
                return used
            inst["inputs"], inst["outputs"] = wired("I", decl["inputs"]), wired("O", decl["outputs"])
        out.append(inst)
    return out


def contract(smw: str | Path, usp_dirs: list[str | Path] | None = None, modules: dict | None = None) -> dict:
    """A check_contract contract from the program's Q-SYS modules, plus what it was built from."""
    table = {**MODULES, **{k.lower(): v for k, v in (modules or {}).items()}}
    comps, used, unknown = {}, [], []
    for inst in read_program(smw, usp_dirs):
        p = inst.get("params", {})
        block = p.get("ComponentName") or next((v for k, v in p.items() if k.lower().startswith("component") and "control" not in k.lower()), None)
        if not block:
            if inst["module"].lower().startswith("qsys") and not inst["found_usp"]:
                unknown.append({"module": inst["module"], "comment": inst["comment"], "why": "its .usp was not found"})
            continue
        desc = table.get(inst["module"].lower())
        controls, writes = [], set()
        if desc is None:
            unknown.append({"module": inst["module"], "comment": inst["comment"], "component": block,
                            "why": "module not described: only the block is checked"})
        else:
            for c in desc.get("controls", []):
                controls.append(re.sub(r"\{(\w+)\}", lambda m: str(p.get(m.group(1), "")), c))
            writes.update(desc.get("writes", []))
            for pin, ctl in desc.get("inputs", {}).items():
                if pin in inst.get("inputs", {}):
                    controls.append(ctl)
                    writes.add(ctl)
            for pin, ctl in desc.get("outputs", {}).items():
                if pin in inst.get("outputs", {}):
                    controls.append(ctl)
            if desc.get("control_param") and p.get(desc["control_param"]):
                controls.append(p[desc["control_param"]])
        entry = comps.setdefault(block, {"controls": []})
        for c in controls:
            known = next((x for x in entry["controls"] if (x if isinstance(x, str) else x["id"]) == c), None)
            item = {"id": c, "write": True} if c in writes else c
            if known is None:
                entry["controls"].append(item)
            elif c in writes and isinstance(known, str):
                entry["controls"][entry["controls"].index(known)] = item
        used.append({"module": inst["module"], "comment": inst["comment"], "component": block, "controls": controls})
    return {"via": "qrc", "components": comps, "modules": used, "not_checked": unknown}
