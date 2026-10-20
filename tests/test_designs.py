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


# ---- Signal Names ---------------------------------------------------------------------------

def gains(*names):
    return [{"name": n, "class": "gain"} for n in names]


def test_validate_accepts_signal_names():
    spec = {"components": gains("MIX", "AEC", "SPK", "AMP"), "wires": [
        ["MIX.out.4", "AEC.in.Reference", "AEC REF"],
        {"from": "MIX.out.4", "to": "SPK.in.1", "signal": "aec ref"},  # same source, names aren't case sensitive
        ["MIX.out.4", "AMP.in.1"],                                    # a source can also have wires
        {"from": "MIX.out.1", "to": "AMP.in.2"},
    ]}
    prepared, problems = designs.validate(spec)
    assert not problems, problems
    assert prepared["wires"][0] == ["MIX.out.4", "AEC.in.Reference", "AEC REF"]


def test_validate_reports_signal_problems():
    spec = {"components": gains("A", "B", "C", "D"), "wires": [
        ["A.out.1", "B.in.1", ""],                              # 0: empty name
        {"from": "A.out.1", "to": "B.in.1", "signal": 5},       # 1: not a string
        ["A.out.1", "B.in.1", "X", "Y"],                        # 2: too long
        ["A.out.1", "B.in.1", "ONE"],                           # 3
        ["A.out.1", "C.in.1", "TWO"],                           # 4: A.out.1 already carries ONE
        ["D.out.1", "C.in.2", "one"],                           # 5: ONE already comes from A.out.1
        ["B.out.1", "D.in.1"],                                  # 6
        ["A.out.1", "D.in.1", "ONE"],                           # 7: D.in.1 already has a wire
        ["C.out.1", "A.in.1", "THREE"],                         # 8
        ["B.out.1", "A.in.1"],                                  # 9: A.in.1 already takes THREE
        ["D.out.2", "B.in.1", "FOUR"],                          # 10: B.in.1 already takes ONE
    ]}
    _, problems = designs.validate(spec)
    assert len(problems) == 8, problems
    text = "\n".join(problems)
    for expected in ("wires[0]: the signal name must be a non-empty string, not ''",
                     "wires[1]: the signal name must be a non-empty string, not 5",
                     "wires[2]: expected [from, to] or [from, to, signal]",
                     "wires[4]: A.out.1 already carries signal 'ONE' (wires[3])",
                     "wires[5]: signal 'one' already comes from A.out.1 (wires[3]); a name has one source (names are not case sensitive)",
                     "wires[7]: D.in.1 already has a wire (wires[6])",
                     "wires[9]: A.in.1 already takes signal 'THREE' (wires[8])",
                     "wires[10]: B.in.1 already takes signal 'ONE' (wires[3])"):
        assert expected in text


def test_validate_signal_pins_by_number_and_name():
    spec = {"components": gains("A", "B", "C"), "wires": [
        ["A.out.1", "B.in.1", "N"],
        ["A.out.Output", "C.in.1", "N"],     # may be A.out.1: left to the build, which resolves pin names
        ["A.out.2", "C.in.2", "N"],          # surely another pin
    ]}
    _, problems = designs.validate(spec)
    assert problems == ["wires[2]: signal 'N' already comes from A.out.1 (wires[0]); a name has one source"]


def labelled(d, labels):
    """Give pins signal names, e.g. {"MIX.out.1": "AEC REF"}."""
    for ref, name in labels.items():
        cname, d_, index = ref.split(".")
        c = next(c for c in d["components"] if c["name"] == cname)
        next(p for p in c["pins"] if p["dir"] == d_ and p["index"] == int(index))["signal"] = name
    return d


def signal_design():
    """The far end reaches the AEC's reference and the speaker through a signal name, with no wires."""
    mic, far, spk = comp("MIC"), comp("FAR"), comp("SPK")
    mix = comp("MIX", "mixer", (("in", 1, "Input 1"), ("out", 1, "Output 1")))
    aec = comp("AEC", "acoustic_echo_canceler_simd",
               (("in", 1, "Microphone"), ("in", 2, "Reference"), ("out", 1, "Conference")))
    tx = comp("TX", "usb_transmitter", (("in", 1, "Channel 1"),))
    d = design([mic, far, mix, aec, tx, spk], [["MIC.out.1", "AEC.in.1"], ["FAR.out.1", "MIX.in.1"], ["AEC.out.1", "TX.in.1"]])
    return labelled(d, {"MIX.out.1": "AEC REF", "AEC.in.2": "aec ref", "SPK.in.1": "AEC REF"})


def test_check_follows_signal_names():
    r = designs.check(signal_design(), [
        {"from": "FAR", "to": "SPK"},
        {"from": "FAR", "to": "AEC.in.Reference"},
        {"from": "FAR", "to": "TX", "expect": False},   # the reference is subtracted, not passed on
        {"from": "MIC", "to": "TX", "via": ["AEC"]},
    ], allow_unconnected=["MIC.in.1", "FAR.in.1", "SPK.out.1"])
    assert r["failed"] == 0, r["results"]
    by = {x["check"]: x for x in r["results"]}
    assert by["signal names: one source each"]["detail"] == "1 names"


def test_check_without_signal_names():
    by = {x["check"]: x for x in designs.check(aec_design())["results"]}
    assert by["signal names: one source each"] == {"check": "signal names: one source each", "pass": True, "detail": "none"}


def test_check_flags_signal_names_without_one_source():
    d = signal_design()
    d["components"].append(comp("REC"))
    labelled(d, {"FAR.out.1": "aec ref", "REC.in.1": "LOST"})
    by = {x["check"]: x for x in designs.check(d)["results"]}
    one = by["signal names: one source each"]
    assert not one["pass"]
    assert sorted(one["detail"]) == ["LOST: no source", "aec ref: 2 sources (FAR.out.1, MIX.out.1)"]
    loose = by["no unexpected unconnected pins"]["detail"]
    assert "REC.in.1 (Input)" in loose and "SPK.in.1 (Input)" not in loose


def test_summarise_keeps_signals():
    d = signal_design()
    d.update(ucis=[], signals=[{"name": "AEC REF", "source": "MIX.out.1", "targets": ["AEC.in.2", "SPK.in.1"]}])
    assert designs.summarise(d)["signals"] == d["signals"]
    assert designs.summarise(d, ["SPK"])["signals"] == d["signals"]
    assert designs.summarise(d, ["MIC"])["signals"] == []
    assert designs.summarise(d, include={"wires"})["signals"] == d["signals"]
    assert "signals" not in designs.summarise(d, include={"pins"})


# ---- schematic graphics -----------------------------------------------------------------------

def test_validate_graphics():
    spec = {"pages": ["Audio", "Control"], "graphics": [
        {"type": "group_box", "x": 20, "y": 20, "w": 900, "h": 400, "text": "Microphones",
         "fill": "#1A4A90E2", "stroke": "#FF4A90E2", "stroke_width": 2, "corner_radius": 6, "align": "Left"},
        {"type": "header", "page": "Control", "text": "CONTROL", "font_size": 20, "bold": True, "align": "center"},
        {"type": "text", "page": "Audio", "text": "AEC REF comes from MIX output 4", "valign": "Top", "color": "#333333"},
    ]}
    assert designs.validate(spec)[1] == []
    spec["graphics"] = [
        {"type": "image"},
        {"type": "text", "page": "Nope"},
        {"type": "header", "fill": "#FF000000", "valign": "Top"},
        {"type": "group_box", "valign": "Top"},
        {"type": "text", "align": "Justify", "color": "red", "w": "wide", "bold": "yes"},
    ]
    _, problems = designs.validate(spec)
    assert problems == [
        "graphics[0]: type must be one of text, header, group_box",
        "graphics[1]: page 'Nope' is not in spec.pages",
        "graphics[2]: a header has no fill",
        "graphics[2]: a header has no valign",
        "graphics[3]: a group_box has no valign",
        "graphics[4]: align must be one of Left, Center, Right",
        "graphics[4]: color 'red' should be a colour like #FF336699 (#AARRGGBB)",
        "graphics[4]: w must be a number",
        "graphics[4]: bold must be true or false",
    ]


def test_summarise_keeps_graphics():
    d = design([comp("MIC")], [])
    d.update(ucis=[], graphics=[{"type": "text", "page": "Main", "x": 0, "y": 0, "w": 200, "h": 40, "text": "note"}])
    assert designs.summarise(d)["graphics"] == d["graphics"]
    assert designs.summarise(d, ["MIC"])["graphics"] == d["graphics"]
    assert "graphics" not in designs.summarise(d, include={"wires"})
