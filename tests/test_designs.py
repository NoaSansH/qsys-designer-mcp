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
    for expected in ("duplicate name G1", "'bad name'", "exactly one of 'class', 'inventory' or 'plugin'", "page 'Nope'",
                     "script_access", "no component MISSING", "should look like NAME.out"):
        assert expected in text


def test_validate_signal_names_graphics_and_banks():
    spec = {"pages": ["A"], "components": [
        {"name": "G1", "class": "gain"},
        {"name": "MIX", "class": "mixer", "control_pins": "active"},
        {"name": "PL", "plugin": "Some Plugin"},
    ], "wires": [["G1.out.1", "MIX.in.1", "Signal 1"], {"from": "G1.out.1", "to": "MIX.in.2", "signal": "Signal 1"}],
        "graphics": [{"type": "text", "text": "note"}, {"type": "circle"}, {"type": "group", "page": "Nope"}],
        "snapshot_banks": [{"name": "Modes", "controls": [{"component": "MIX", "controls": ["input_1_output_1_mute"]},
                                                          {"component": "GHOST", "controls": ["mute"]}],
                            "controller": {"name": "G1"}},
                           {"name": "Other", "controller": {"name": "Modes_2"}}],
        "ucis": [{"name": "U", "pages": [{"name": "P", "elements": [{"component": "Modes_2", "control": "load_1"}]}]}]}
    _, problems = designs.validate(spec)
    text = "\n".join(problems)
    for expected in ("MIX: control_pins must be a list", "type must be text or group", "page 'Nope'",
                     "snapshot bank Modes: no component GHOST", "controller name 'G1' must be a new"):
        assert expected in text
    assert not any("wires" in p for p in problems)          # three-element wires and signal objects are fine
    assert not any("PL:" in p for p in problems)            # plugin components need no class
    assert not any("UCI" in p for p in problems)            # Snapshot Controllers can be used on UCIs


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
