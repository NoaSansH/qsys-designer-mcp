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


def test_validate_accepts_plugins():
    spec = {"components": [
        {"name": "DEV_1", "plugin": "My Device", "properties": {"Debug Print": "All"}, "values": {"IPAddress": "192.168.1.100"}},
        {"name": "DEV_2", "plugin": {"name": "Custom~My Device", "version": "0.1.0"}},
        {"name": "DEV_3", "plugin": {"id": "6f0c1d2e-0000-4000-8000-000000000001"}},
    ]}
    prepared, problems = designs.validate(spec)
    assert not problems
    assert prepared["components"][1]["plugin"] == {"name": "Custom~My Device", "version": "0.1.0"}


def test_validate_reports_plugin_problems():
    spec = {"components": [
        {"name": "A", "plugin": "My Device", "class": "gain"},
        {"name": "B", "plugin": {"version": "1.0"}},
        {"name": "C", "plugin": {"name": "My Device", "version": 1.1}},
        {"name": "D", "plugin": {"name": "My Device", "model": "x"}},
        {"name": "E", "plugin": ["My Device"]},
        {"name": "F", "plugin": "My Device", "properties": {"plugin_source": "print(1)", "plugin_is_encrypted": False}},
        {"name": "G", "class": "%PLUGIN%_my-device"},
        {"name": "H", "plugin": "   "},
    ]}
    _, problems = designs.validate(spec)
    text = "\n".join(problems)
    for expected in ("A: give exactly one of 'class', 'inventory' or 'plugin'", "B: plugin needs a name or an id",
                     "C: plugin version must be a non-empty string", "D: plugin has unknown keys ['model']",
                     "E: plugin must be the plugin's name or id", "F: property plugin_source comes from the installed plugin",
                     "F: property plugin_is_encrypted comes from", "G: class '%PLUGIN%_my-device' is a plugin",
                     '"plugin": {"id": "my-device"}', "H: plugin is empty"):
        assert expected in text


def test_summarise_lists_plugins():
    info = {"name": "Custom~My Device", "id": "my-device", "version": "0.1.0", "encrypted": False}
    dev1 = dict(comp("DEV_1", "%PLUGIN%_my-device"), plugin=dict(info),
                properties={"Debug Print": "All", "plugin_source": "(2048 characters, not shown)"})
    dev2 = dict(comp("DEV_2", "%PLUGIN%_my-device"), plugin=dict(info))
    old = dict(comp("DEV_OLD", "%PLUGIN%_my-device"), plugin=dict(info, version="0.0.9"))
    d = designs.summarise(design([dev1, comp("G"), dev2, old], []), include={"pins"})
    assert d["plugins"] == [dict(info, components=["DEV_1", "DEV_2"]), dict(info, version="0.0.9", components=["DEV_OLD"])]
    by = {c["name"]: c for c in d["components"]}
    assert by["DEV_1"]["plugin"] == info and "properties" not in by["DEV_1"]
    assert "plugin" not in by["G"]
    filtered = designs.summarise(design([dev1, comp("G")], []), components=["G"], include={"pins"})
    assert filtered["plugins"][0]["components"] == ["DEV_1"]   # the design's plugins, whatever is shown
    assert "plugins" not in designs.summarise(design([comp("G")], []), include={"pins"})


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
