import asyncio
import json

import pytest

from qsys_mcp import designs, templates


def test_template_included_twice_with_prefix_params_and_offset(tmp_path):
    (tmp_path / "room.json").write_text(json.dumps({
        "params": {"MAX": 0},
        "components": [{"name": "MIC", "class": "gain", "x": 0, "y": 0, "label": "${ROOM} mic",
                        "properties": {"max_gain": "${MAX}"}},
                       {"name": "MIX", "class": "mixer", "x": 200, "y": 0}],
        "wires": [["MIC.out.1", "MIX.in.1", "Mic"], ["SHARED.out.1", "MIX.in.2"]],
        "snapshot_banks": [{"name": "Modes", "controls": [{"component": "MIX", "controls": ["input_1_output_1_mute"]}],
                            "controller": {"name": "Modes", "x": 0, "y": 100}}],
        "named_controls": [{"name": "Vol", "component": "MIC", "control": "gain"}]}), encoding="utf-8")
    spec = {"components": [{"name": "SHARED", "class": "gain", "x": 0, "y": 0}],
            "include": [{"file": "room.json", "prefix": "R1_", "params": {"ROOM": "Salle 1"}},
                        {"file": "room.json", "prefix": "R2_", "params": {"ROOM": "Salle 2", "MAX": 10}, "dy": 500, "page": "R2"}]}
    out = templates.expand(spec, tmp_path)
    names = [c["name"] for c in out["components"]]
    assert names == ["SHARED", "R1_MIC", "R1_MIX", "R2_MIC", "R2_MIX"]
    r2 = out["components"][3]
    assert r2["label"] == "Salle 2 mic" and r2["properties"]["max_gain"] == 10 and r2["y"] == 500 and r2["page"] == "R2"
    assert ["R2_MIC.out.1", "R2_MIX.in.1", "R2_Mic"] in out["wires"]
    assert ["SHARED.out.1", "R2_MIX.in.2"] in out["wires"]          # blocks outside the template keep their name
    assert [b["controller"]["name"] for b in out["snapshot_banks"]] == ["R1_Modes", "R2_Modes"]
    assert out["named_controls"][1] == {"name": "R2_Vol", "component": "R2_MIC", "control": "gain"}
    assert "R2" in out["pages"] and not designs.validate(out)[1]


def test_template_missing_param_is_reported(tmp_path):
    (tmp_path / "t.json").write_text(json.dumps({"components": [{"name": "A", "class": "gain", "label": "${NOPE}"}]}), encoding="utf-8")
    with pytest.raises(designs.SpecError, match="NOPE"):
        templates.expand({"include": [{"file": "t.json"}]}, tmp_path)


def test_real_core_needs_allow_changes():
    from qsys_mcp import server
    from qsys_mcp.qrc.client import QrcClient, QrcError
    saved = server.qrc
    try:
        server.qrc = QrcClient(host="10.20.30.40")
        server.live_changes_allowed = False
        with pytest.raises(QrcError, match="real Core"):
            server._check_write()
        server.live_changes_allowed = True
        server._check_write()
        server.qrc = QrcClient(host="127.0.0.1")
        server.live_changes_allowed = False
        server._check_write()                                      # the emulator is always fine
    finally:
        server.qrc, server.live_changes_allowed = saved, False
    assert server.emulator.host == "127.0.0.1"


def test_lua_script_runs_with_timers_and_other_blocks():
    from qsys_mcp import luatest
    try:
        luatest.find_lua()
    except FileNotFoundError:
        pytest.skip("no Lua interpreter")
    code = '''
mix = Component.New("MIX")
Controls.Mode.EventHandler = function(c)
  mix["input_1_mute"].Boolean = c.Value == 0
  Timer.CallAfter(function() Controls.Ready.Boolean = true end, 2)
end
t = Timer.New()
t.EventHandler = function() Controls.Ticks.Value = Controls.Ticks.Value + 1 end
t:Start(1)
print("started")
'''
    controls = [{"name": "Mode", "type": "knob", "min": 0, "max": 2}, {"name": "Ready", "type": "led"},
                {"name": "Ticks", "type": "knob", "min": 0, "max": 100}]
    r = luatest.run(code, controls, [
        {"set": {"control": "Mode", "value": 0}},
        {"expect": {"component": "MIX", "control": "input_1_mute", "value": True}},
        {"expect": {"control": "Ready", "value": False}},
        {"advance": 2.5},
        {"expect": {"control": "Ready", "value": True}},
        {"expect": {"control": "Ticks", "value": 2}},
        {"expect": {"control": "Ticks", "value": 99}, "label": "wrong on purpose"}])
    assert r["checks"] == 5 and r["passed"] == 4 and r["failures"][0]["label"] == "wrong on purpose"
    assert r["log"] == ["started"]
