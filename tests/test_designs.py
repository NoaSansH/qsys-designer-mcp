import copy

import pytest

from qsys_mcp import designs


def comp(name, cls="gain", pins=(("in", 1, "Input"), ("out", 1, "Output"))):
    return {"name": name, "class": cls, "pins": [{"dir": d, "index": i, "name": n} for d, i, n in pins], "values": {}}


def design(components, wires):
    return {"components": components, "wires": wires}


def test_validate_reports_problems():
    spec = {"pages": ["A"], "components": [
        {"name": "G1", "class": "gain"},
        {"name": "G1", "class": "gain"},
        {"name": "bad name", "class": "gain"},
        {"name": "X", "class": "gain", "inventory": "Flex In"},
        {"name": "P", "class": "gain", "page": "Nope"},
        {"name": "S", "class": "gain", "script_access": "Everything"},
    ], "wires": [["G1.out.1", "MISSING.in.1"], ["G1.in.1", "G1.out.1"]]}
    _, problems = designs.validate(spec)
    text = "\n".join(problems)
    for expected in ("duplicate name G1", "'bad name'", "exactly one of 'class' or 'inventory'", "page 'Nope'",
                     "script_access", "no component MISSING", "should look like NAME.out"):
        assert expected in text


def test_validate_inlines_lua_file(tmp_path):
    (tmp_path / "x.lua").write_text("print('hi')", encoding="utf-8")
    spec = {"components": [{"name": "TC", "class": "device_controller_script",
                            "text_controller": {"controls": [], "lua_file": "x.lua"}}]}
    prepared, problems = designs.validate(spec, tmp_path)
    assert not problems
    assert prepared["components"][0]["text_controller"]["lua"] == "print('hi')"
    assert "lua_file" not in prepared["components"][0]["text_controller"]
    assert "lua_file" in spec["components"][0]["text_controller"]  # caller's spec untouched


def test_validate_rejects_bad_lua():
    spec = {"components": [{"name": "TC", "class": "device_controller_script",
                            "text_controller": {"controls": [], "lua": "function ("}}]}
    _, problems = designs.validate(spec)
    assert any("Lua syntax error" in p for p in problems)


def test_build_refuses_existing_file(tmp_path):
    p = tmp_path / "x.qsys"
    p.write_bytes(b"")
    with pytest.raises(FileExistsError):
        designs.build({"components": []}, str(p))


def aec_design():
    mic = comp("MIC", "gain")
    far = comp("FAR", "gain")
    aec = comp("AEC", "acoustic_echo_canceler_simd",
               (("in", 1, "Microphone"), ("in", 2, "Reference"), ("out", 1, "Conference")))
    tx = comp("TX", "usb_transmitter", (("in", 1, "Channel 1"),))
    spk = comp("SPK", "gain")
    wires = [["MIC.out.1", "AEC.in.1"], ["FAR.out.1", "AEC.in.2"], ["FAR.out.1", "SPK.in.1"], ["AEC.out.1", "TX.in.1"]]
    return design([mic, far, aec, tx, spk], wires)


def test_check_paths_and_aec_reference_is_not_a_path():
    d = aec_design()
    r = designs.check(d, [
        {"from": "MIC", "to": "TX", "via": ["AEC"]},
        {"from": "FAR", "to": "TX", "expect": False},          # far end only reaches the AEC's reference
        {"from": "FAR", "to": "AEC.in.Reference"},
        {"from": "MIC", "to": "SPK", "expect": False},
    ], allow_unconnected=["MIC.in.1", "FAR.in.1", "SPK.out.1"])
    assert r["failed"] == 0, r["results"]


def test_check_finds_failures():
    d = aec_design()
    r = designs.check(d, [{"from": "FAR", "to": "TX"}, {"from": "MIC", "to": "TX", "via": ["SPK"]}])
    by = {x["check"]: x for x in r["results"]}
    assert not by["FAR reaches TX"]["pass"]
    assert not by["MIC reaches TX"]["pass"] and by["MIC reaches TX"]["detail"]["missing_via"] == ["SPK"]
    assert not by["no unexpected unconnected pins"]["pass"]


def test_check_flags_missing_positions():
    d = aec_design()
    d["components"][0]["values"] = {"gain": {"value": -6, "position": None}}
    r = designs.check(d)
    assert not next(x for x in r["results"] if x["check"] == "every stored value has a position")["pass"]


def with_control_pins(c, *pins):
    """Add control pins (dir, control code name, pin name) as read_design reports them, after the block's own."""
    for d, control, name in pins:
        index = sum(p["dir"] == d for p in c["pins"]) + 1
        c["pins"].append({"dir": d, "index": index, "name": name, "domain": "control", "user_created": True, "control": control})
    return c


def test_validate_control_pins():
    spec = {"components": [
        {"name": "G", "class": "gain", "control_pins": ["bypass", {"control": "mute", "dir": "both"}, {"control": "gain", "dir": "in"}]},
        {"name": "H", "class": "gain", "control_pins": [{"control": "mute"}]},
        {"name": "TC", "class": "device_controller_script",
         "text_controller": {"controls": [{"name": "Power", "pins": "Both"}, {"name": "Status"}]}},
    ], "wires": [["G.out.mute", "H.in.mute"], ["TC.out.Power", "G.in.gain"], ["H.out.Mute", "TC.in.power"]]}
    _, problems = designs.validate(spec)
    assert not problems, problems


def test_validate_reports_control_pin_problems():
    spec = {"components": [
        {"name": "A", "class": "gain", "control_pins": "mute"},
        {"name": "B", "class": "gain", "control_pins": [42, {"dir": "in"}, {"control": "mute", "direction": "in"},
                                                         {"control": "gain", "dir": "input"}, "bypass", "bypass"]},
        {"name": "C", "class": "gain", "control_pins": [{"control": "gain", "dir": "in"}, {"control": "peak", "dir": "out"}]},
        {"name": "TC", "class": "device_controller_script", "control_pins": ["Power"],
         "text_controller": {"controls": [{"name": "Power", "pins": "In"}, {"name": "Status"}]}},
    ], "wires": [["C.out.gain", "C.in.peak"], ["TC.out.Status", "C.in.gain"]]}
    _, problems = designs.validate(spec)
    text = "\n".join(problems)
    for expected in ("A: control_pins must be a list", "B.control_pins[0]: give a control code name",
                     "B.control_pins[1]: no control code name", "B.control_pins[2]: unknown key(s) direction",
                     "B.control_pins[3]: dir must be in, out or both, not 'input'", "control bypass is listed twice",
                     "'C.out.gain': gain only has an input pin", "'C.in.peak': peak only has an output pin",
                     "pins must be None, Input, Output or Both, not 'In'", "Power is one of the Text Controller's own controls",
                     "'TC.out.Status': the Text Controller control Status has no pins"):
        assert expected in text, expected


def control_design():
    """SRC feeds G (to OUT) and a signal presence detector; the detector drives TC, which mutes G."""
    src = comp("SRC")
    g = with_control_pins(comp("G"), ("in", "mute", "Mute"), ("out", "mute", "Mute"), ("in", "gain", "Gain"))
    det = with_control_pins(comp("DET", "signal_presence", (("in", 1, "Input"),)),
                            ("out", "signal_presence", "Signal Presence"))
    det["pins"][0]["domain"] = "audio"   # as read_design reports it; a pin with no domain is a signal pin too
    tc = with_control_pins(comp("TC", "device_controller_script", ()), ("in", "Speech", "Speech"), ("out", "Mute", "Mute"))
    out = comp("OUT")
    # a logic block's own control pins: not added for a control, so never "unused"
    gate = comp("GATE", "control_logic_and", (("in", 1, "Input 1"), ("in", 2, "Input 2"), ("out", 1, "Output")))
    for p in gate["pins"]:
        p["domain"] = "control"
    gate["pins"][0]["control"] = "input_1"
    wires = [["SRC.out.1", "G.in.1"], ["SRC.out.1", "DET.in.1"], ["G.out.1", "OUT.in.1"],
             ["DET.out.1", "TC.in.1"], ["G.in.2", "TC.out.1"]]   # the last one saved input first
    return design([src, g, det, tc, out, gate], wires)


def test_read_adds_control_pins_and_orders_wires(monkeypatch, tmp_path):
    f = tmp_path / "x.qsys"
    f.write_bytes(b"")
    raw = control_design()
    monkeypatch.setattr(designs.headless, "pick", lambda *a, **k: None)
    monkeypatch.setattr(designs.headless, "run", lambda req, inst: copy.deepcopy(raw))
    monkeypatch.setattr(designs.headless, "file_designer_version", lambda p: None)
    d = designs.read(str(f))
    by = {c["name"]: c for c in d["components"]}
    assert by["G"]["control_pins"] == [{"control": "mute", "dir": "both"}, {"control": "gain", "dir": "in"}]
    assert by["DET"]["control_pins"] == [{"control": "signal_presence", "dir": "out"}]
    assert "control_pins" not in by["SRC"] and "control_pins" not in by["GATE"]
    assert d["wires"][-1] == ["TC.out.1", "G.in.2"]
    s = designs.summarise(d, include={"wires"})
    assert s["wire_count"] == 5 and s["control_wire_count"] == 2


def test_check_control_pins_and_wires():
    d = control_design()
    r = designs.check(d, [
        {"from": "SRC", "to": "OUT", "via": ["G"]},
        {"from": "SRC", "to": "TC", "expect": False},                  # signal never turns into control
        {"from": "DET.out.signal_presence", "to": "G", "via": ["TC"]},  # control pins by control code name
        {"from": "TC", "to": "G.in.mute"},
        {"from": "DET.out.Signal Presence", "to": "OUT", "expect": False},
    ], allow_unconnected=["SRC.in.1", "OUT.out.1"])
    by = {x["check"]: x for x in r["results"]}
    assert by["no unexpected unconnected pins"]["pass"], by["no unexpected unconnected pins"]
    assert not by["no unused control pins"]["pass"]
    assert by["no unused control pins"]["detail"] == ["G.gain (in)"]   # mute is used on one side, which is enough
    assert r["failed"] == 1, [x for x in r["results"] if not x["pass"]]
    r = designs.check(d, allow_unconnected=["SRC.in.1", "OUT.out.1", "G.in.gain"])
    assert r["failed"] == 0, [x for x in r["results"] if not x["pass"]]


def test_check_follows_wires_saved_input_first():
    d = aec_design()
    d["wires"] = [[b, a] if a.startswith("MIC") else [a, b] for a, b in d["wires"]]
    r = designs.check(d, [{"from": "MIC", "to": "TX", "via": ["AEC"]}])
    assert next(x for x in r["results"] if x["check"] == "MIC reaches TX")["pass"]
