"""add_text_controls: controls added to a saved Text Controller; its other controls, their wires,
its Lua and Script Access stay. A name already used, or a block that isn't a Text Controller, is refused."""
import pytest

from qsys_mcp import designs, headless

INSTALLS = headless.installs()
LUA = "Controls.Out.Value = Controls.In.Value"
SPEC = {"components": [
    {"name": "SRC", "class": "gain", "x": 0, "y": 0},
    {"name": "TC", "class": "device_controller_script", "x": 300, "y": 0, "script_access": "External",
     "text_controller": {"controls": [{"name": "In", "type": "led", "pins": "Input"},
                                      {"name": "Out", "type": "led"}], "lua": LUA}}]}


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
def test_add_text_controls(inst, tmp_path):
    p = tmp_path / "tc.qsys"
    designs.build(SPEC, str(p), designer_version=inst.version)
    designs.edit(str(p), [{"op": "add_control_pins", "component": "SRC", "controls": ["mute"]},
                          {"op": "add_wire", "from": "SRC.out.mute", "to": "TC.in.In"}])
    wires = designs.read(str(p))["wires"]
    r = designs.edit(str(p), [{"op": "add_text_controls", "component": "TC",
                               "controls": [{"name": "Beat", "type": "led"}, {"name": "Level", "type": "knob", "unit": "dB", "min": -10, "max": 0}]}])
    assert r["applied"][0]["result"]["controls"] == ["In", "Out", "Beat", "Level"]
    d = designs.read(str(p))
    tc = next(c for c in d["components"] if c["name"] == "TC")
    assert tc["script_access"] == "External"
    assert tc["values"]["code"]["string"] == LUA
    assert d["wires"] == wires
    assert '"Name":"Beat"' in tc["properties"]["config"] and '"Name":"Level"' in tc["properties"]["config"]
    with pytest.raises(headless.HelperError, match="already has a control named Beat"):
        designs.edit(str(p), [{"op": "add_text_controls", "component": "TC", "controls": [{"name": "Beat", "type": "led"}]}])
    with pytest.raises(headless.HelperError, match="not a Text Controller"):
        designs.edit(str(p), [{"op": "add_text_controls", "component": "SRC", "controls": [{"name": "X", "type": "led"}]}])
    with pytest.raises(designs.SpecError, match="needs controls"):
        designs.edit(str(p), [{"op": "add_text_controls", "component": "TC", "controls": []}])
