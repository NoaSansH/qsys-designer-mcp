"""set_uci_element: one element of a saved UCI moved, resized or relabelled, matched by its text or
by its control; a match that finds none or several is refused."""
import pytest

from qsys_mcp import designs, headless

INSTALLS = headless.installs()
SPEC = {"components": [{"name": "VOL", "class": "gain", "x": 0, "y": 0}],
        "ucis": [{"name": "Panel", "pages": [{"name": "Main", "elements": [
            {"type": "text", "text": "Level (dBFS)", "x": 10, "y": 10, "w": 200, "h": 20},
            {"type": "text", "text": "Twice", "x": 10, "y": 40, "w": 50, "h": 20},
            {"type": "text", "text": "Twice", "x": 70, "y": 40, "w": 50, "h": 20},
            {"type": "control", "component": "VOL", "control": "gain", "style": "Fader", "x": 10, "y": 70, "w": 60, "h": 200}]}]}]}


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
def test_move_resize_and_relabel(inst, tmp_path):
    p = tmp_path / "panel.qsys"
    designs.build(SPEC, str(p), designer_version=inst.version)
    r = designs.edit(str(p), [
        {"op": "set_uci_element", "uci": "Panel", "match": {"text": "Level (dBFS)"}, "set": {"text": "Level", "w": 80}},
        {"op": "set_uci_element", "uci": "Panel", "page": "Main", "match": {"component": "VOL", "control": "gain"}, "set": {"x": 300}}])
    assert r["applied"][0]["result"]["was"] == {"x": 10.0, "y": 10.0, "w": 200.0, "h": 20.0}
    els = designs.read(str(p))["ucis"][0]["pages"][0]["elements"]
    assert [(e["x"], e["w"]) for e in els if e.get("label") == "Level"] == [(10, 80)]
    assert [e["x"] for e in els if e.get("component") == "VOL"] == [300]
    for match in ({"text": "Twice"}, {"text": "Nowhere"}):
        with pytest.raises(headless.HelperError, match="elements match"):
            designs.edit(str(p), [{"op": "set_uci_element", "uci": "Panel", "match": match, "set": {"x": 0}}])
