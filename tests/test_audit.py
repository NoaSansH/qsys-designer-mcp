from qsys_mcp import audit


def pin(d, i, n):
    return {"dir": d, "index": i, "name": n}


def comp(name, cls, pins, values=None, **kw):
    return {"name": name, "class": cls, "pins": pins, "values": values or {}, "properties": kw.get("properties", {}),
            "script_access": kw.get("access", "None")}


def room(mix_values):
    """mic -> MIX in1, usb in -> MIX in2; MIX out1 -> VOL -> speakers, out2 -> usb out; VOL -> REF."""
    return {"components": [
        comp("MIC", "io_card_mic_line_in_x", [pin("out", 1, "Channel 1")]),
        comp("USB_IN", "usb_receiver", [pin("out", 1, "Channel 1")], properties={"bridge_name": "PC"}),
        comp("USB_OUT", "usb_transmitter", [pin("in", 1, "Channel 1")], properties={"bridge_name": "PC"}),
        comp("MIX", "mixer", [pin("in", 1, "Input 1"), pin("in", 2, "Input 2"), pin("out", 1, "Output 1"), pin("out", 2, "Output 2")],
             mix_values),
        comp("VOL", "gain", [pin("in", 1, "Input"), pin("out", 1, "Output")], {"gain": {"value": 0}, "mute": {"value": 0}}),
        comp("SPK", "io_card_flex_out_core", [pin("in", 1, "Channel 1")]),
        comp("REF", "soft_dante_output", [pin("in", 1, "Input 1")]),
        comp("LIM", "limiter_peak", [pin("in", 1, "Input"), pin("out", 1, "Output")], {"threshold_level": {"value": 3}})],
        "wires": [["MIC.out.1", "MIX.in.1"], ["USB_IN.out.1", "MIX.in.2"], ["MIX.out.1", "VOL.in.1"], ["VOL.out.1", "SPK.in.1"],
                  ["VOL.out.1", "REF.in.1"], ["MIX.out.2", "USB_OUT.in.1"], ["LIM.out.1", "LIM.in.1"]]}


def closed():
    return {f"input_{i}_output_{o}_mute": {"value": 1} for i in (1, 2) for o in (1, 2)}


def test_roles_are_inferred():
    r = audit.infer_roles(room(closed()))
    assert r == {"mics": ["MIC"], "speakers": ["SPK"], "pc_links": [["USB_IN", "USB_OUT"]]}


def test_clean_room_then_faults_in_snapshots():
    d = room(closed())
    states = {"Modes": {"1": {"MIX.input_2_output_1_mute": "unmuted", "MIX.input_1_output_2_mute": "unmuted"},   # fine
                        "2": {"MIX.input_1_output_1_mute": "unmuted"},                                            # mic -> speakers
                        "3": {"MIX.input_2_output_2_mute": "unmuted"},                                            # PC loop
                        "4": {"MIX.input_1_output_1_mute": "unmuted", "MIX.input_1_output_1_gain": "-100dB"}}}     # open but -100 dB
    r = audit.audit(d, {"aec_reference": "REF.in.1", "volume": "VOL"}, states)
    errors = {(f["rule"].split(" ")[0], f["state"]) for f in r["findings"] if f["severity"] == "error"}
    assert errors == {("microphone", "Modes 2"), ("a", "Modes 3")}
    assert any(f["rule"].startswith("limiter threshold") for f in r["findings"])


def test_reference_before_the_volume_is_flagged():
    d = room(closed())
    d["wires"] = [w for w in d["wires"] if w != ["VOL.out.1", "REF.in.1"]] + [["MIX.out.1", "REF.in.1"]]
    r = audit.audit(d, {"aec_reference": "REF.in.1", "volume": "VOL"})
    assert any(f["rule"].startswith("AEC reference") for f in r["findings"])
