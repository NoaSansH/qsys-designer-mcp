from qsys_mcp import layout


def spec():
    return {"layout": "auto", "zones": {"IN": {"text": "sources"}}, "components": [
        {"name": "MIC", "class": "gain", "zone": "IN", "note": "ceiling mic"},
        {"name": "USB", "class": "gain", "zone": "IN"},
        {"name": "MIX", "class": "mixer", "zone": "MIX"},
        {"name": "VOL", "class": "gain", "zone": "OUT", "note": "room volume\npanel"},
        {"name": "LIM", "class": "limiter_peak", "zone": "OUT"},
        {"name": "CTL", "class": "device_controller_script"}],
        "wires": [["MIC.out.1", "MIX.in.1"], ["USB.out.1", "MIX.in.2"], ["MIX.out.1", "VOL.in.1"], ["VOL.out.1", "LIM.in.1"],
                  ["LIM.out.1", "MIX.in.3"]],   # a loop back into the mixer
        "signals": [["USB.out.1", "CTL.in.1", "usb"]]}


def boxes(s):
    return {c["name"]: (c["x"], c["y"]) + layout.estimate(c, {}) for c in s["components"]}


def test_blocks_follow_the_signal_flow():
    s = layout.auto_layout(spec())
    b = boxes(s)
    assert b["MIC"][0] == b["USB"][0] < b["MIX"][0] < b["VOL"][0] < b["LIM"][0]
    assert "layout" not in s and "zones" not in s and all("zone" not in c and "note" not in c for c in s["components"])


def test_blocks_dont_overlap_and_notes_sit_under_their_block():
    s = layout.auto_layout(spec())
    b = boxes(s)
    names = list(b)
    for i, m in enumerate(names):
        for n in names[i + 1:]:
            x1, y1, w1, h1 = b[m]
            x2, y2, w2, h2 = b[n]
            assert x1 + w1 <= x2 or x2 + w2 <= x1 or y1 + h1 <= y2 or y2 + h2 <= y1, (m, n)
    notes = [g for g in s["graphics"] if g["type"] == "text" and g["text"] == "ceiling mic"]
    assert len(notes) == 1 and notes[0]["x"] == b["MIC"][0] and notes[0]["y"] > b["MIC"][1]


def test_zones_frame_their_blocks():
    s = layout.auto_layout(spec())
    b = boxes(s)
    frames = {g["text"]: g for g in s["graphics"] if g["type"] == "group"}
    assert set(frames) == {"IN", "MIX", "OUT"}
    for name, zone in (("MIC", "IN"), ("USB", "IN"), ("VOL", "OUT"), ("LIM", "OUT")):
        f, (x, y, w, h) = frames[zone], b[name]
        assert f["x"] <= x and x + w <= f["x"] + f["w"] and f["y"] <= y and y + h <= f["y"] + f["h"], (name, zone)
    assert any(g["text"] == "sources" for g in s["graphics"])


def test_placed_blocks_stay_put_unless_layout_is_auto():
    s = spec()
    del s["layout"]
    for c in s["components"]:
        c.update(x=1, y=2)
    out = layout.auto_layout(s)
    assert all((c["x"], c["y"]) == (1, 2) for c in out["components"])
    assert any(g["text"] == "ceiling mic" for g in out["graphics"])   # notes still placed
