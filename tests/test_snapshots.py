import copy

import pytest

from qsys_mcp import designs, headless
from qsys_mcp.qrc.client import QrcError

SPEC = {
    "pages": ["Main"],
    "components": [
        {"name": "MIXER", "class": "mixer", "properties": {"n_inputs": 2, "n_outputs": 2}},
        {"name": "MIC_GAIN", "class": "gain"},
        {"name": "PROGRAM_EQ", "class": "equalizer_parametric"},
    ],
    "snapshots": [{
        "name": "Room_Modes", "count": 3,
        "members": ["MIXER.input_1_mute", {"component": "MIC_GAIN", "controls": ["gain", "mute"]},
                    {"component": "PROGRAM_EQ", "all": True}],
        "controller": {"page": "Main", "x": 400, "y": 40, "script_access": "Script"},
        "values": {"1": {"MIXER.input_1_mute": True, "MIC_GAIN.gain": -6},
                   "2": {"MIXER.input_1_mute": False, "PROGRAM_EQ.bypass": True}},
    }],
    "ucis": [{"name": "Panel", "pages": [{"name": "Main", "elements": [
        {"type": "control", "component": "Room_Modes", "control": "load_1", "x": 0, "y": 0, "w": 100, "h": 40}]}]}],
}

# the bank as read_design reports it
READ_BANK = {"name": "Room_Modes", "id": "6f1c2a9e-0d5b-4c1e-9a43-2b7f0e8d1c55", "count": 3, "stored": [],
             "members": [{"component": "MIXER", "all": False, "controls": ["input_1_mute"]},
                         {"component": "MIC_GAIN", "all": False, "controls": ["gain", "mute"]},
                         {"component": "PROGRAM_EQ", "all": True, "controls": []}],
             "controller": {"name": "Room_Modes", "label": "Room_Modes", "page": "Main", "x": 400.0, "y": 40.0,
                            "script_access": "Script"}}


@pytest.fixture
def runtime():
    return pytest.importorskip("qsys_mcp.runtime")   # it loads the Designer app driver (pywinauto, Windows)


def test_validate_accepts_snapshot_banks():
    prepared, problems = designs.validate(SPEC)
    assert not problems
    assert prepared["snapshots"] == SPEC["snapshots"]


def test_validate_reports_snapshot_problems():
    spec = {"components": [{"name": "MIXER", "class": "mixer"}, {"name": "G", "class": "gain"}], "snapshots": [
        {"name": "MIXER", "count": 2, "members": []},
        {"name": "bad name", "count": 0, "members": ["G"]},
        {"name": "B1", "count": 2, "members": ["NOPE.gain", {"component": "G"}, {"component": "MIXER", "all": True, "controls": ["x"]}],
         "controller": {"page": "Other", "script_access": "Everything"}},
        {"name": "B2", "count": 2, "members": ["G.gain", {"component": "G", "all": True}]},
        {"name": "B3", "count": 2, "members": ["G.gain"], "values": {"3": {"G.gain": 0}, "1": {"G.mute": True, "G.gain": [1]}}},
        {"name": "B3", "count": 1, "members": []},
    ], "ucis": [{"name": "P", "pages": [{"name": "M", "elements": [{"component": "NO_BANK", "control": "load_1"}]}]}]}
    _, problems = designs.validate(spec)
    text = "\n".join(problems)
    for expected in ("name MIXER is already used", "'bad name'", "count must be", "member 'G' should look like",
                     "no component NOPE", "member G: give a list", "member MIXER: give a list",
                     "controller page 'Other'", "controller script_access", "listed both whole",
                     "values key '3'", "snapshot 1: G.mute is not a member", "G.gain must be a number",
                     "name B3 is already used", "no component NO_BANK"):
        assert expected in text, expected


def test_build_points_to_save_snapshots(monkeypatch, tmp_path):
    inst = headless.Install(tmp_path, "9.13.2.0")
    monkeypatch.setattr(headless, "pick", lambda *a, **kw: inst)
    monkeypatch.setattr(headless, "load_positions", lambda i: [])
    seen = {}

    def fake_run(req, i):
        seen.update(req)
        return {"components": 3, "values": [], "warnings": [],
                "snapshots": {"banks": [{"name": "Room_Modes", "count": 3, "values": designs.SNAPSHOT_VALUES_PENDING}]}}

    monkeypatch.setattr(headless, "run", fake_run)
    res = designs.build(SPEC, str(tmp_path / "x.qsys"))
    assert "save_snapshots" in res["next_step"] and "Room_Modes" in res["next_step"]
    assert seen["spec"]["snapshots"][0]["members"][0] == "MIXER.input_1_mute"

    # a prebuilt helper from before snapshots ignores the key: say so rather than pass silently
    monkeypatch.setattr(headless, "run", lambda req, i: {"components": 3, "values": [], "warnings": []})
    with pytest.raises(headless.HelperError, match="without its snapshot banks"):
        designs.build(SPEC, str(tmp_path / "y.qsys"))


def test_summarise_keeps_and_filters_snapshots():
    design = {"components": [{"name": "MIXER", "class": "mixer", "pins": []}], "wires": [], "ucis": [],
              "snapshots": {"banks": [READ_BANK, {"name": "Other", "members": [{"component": "X", "all": True, "controls": []}]}],
                            "global": {"count": 4, "stored": [0]}}}
    assert designs.summarise(design)["snapshots"] == design["snapshots"]
    assert "snapshots" not in designs.summarise(design, include={"wires"})
    assert [b["name"] for b in designs.summarise(design, components=["MIC_GAIN"])["snapshots"]["banks"]] == ["Room_Modes"]
    assert [b["name"] for b in designs.summarise(design, components=["Other"])["snapshots"]["banks"]] == ["Other"]


def test_snapshot_values_from_a_spec_its_list_or_a_mapping():
    want = {"Room_Modes": {1: {"MIXER.input_1_mute": True, "MIC_GAIN.gain": -6},
                           2: {"MIXER.input_1_mute": False, "PROGRAM_EQ.bypass": True}}}
    assert designs.snapshot_values(SPEC) == want
    assert designs.snapshot_values(SPEC["snapshots"]) == want
    assert designs.snapshot_values({"Room_Modes": SPEC["snapshots"][0]["values"]}) == want
    # an empty snapshot is the design's own values
    assert designs.snapshot_values({"Room_Modes": {"3": {}}}) == {"Room_Modes": {3: {}}}
    with pytest.raises(designs.SpecError, match="no snapshots"):
        designs.snapshot_values({"components": []})
    with pytest.raises(designs.SpecError, match="snapshot number"):
        designs.snapshot_values({"Room_Modes": {"first": {}}})


def test_snapshot_plan_checks_values_against_the_saved_banks():
    design = {"snapshots": {"banks": [READ_BANK]}}
    plan, problems = designs.snapshot_plan(design, designs.snapshot_values(SPEC))
    assert not problems
    assert plan == [{"bank": "Room_Modes", "controller": "Room_Modes", "count": 3, "snapshots": {
        1: [("MIXER", "input_1_mute", True), ("MIC_GAIN", "gain", -6)],
        2: [("MIXER", "input_1_mute", False), ("PROGRAM_EQ", "bypass", True)]}}]
    plan, _ = designs.snapshot_plan(design, {"Room_Modes": {3: {}}})
    assert plan[0]["snapshots"] == {3: []}
    _, problems = designs.snapshot_plan(design, {"Room_Modes": {4: {"MIC_GAIN.gain": 0}, 1: {"MIXER.input_2_mute": True}},
                                                 "Other": {1: {"X.y": 1}}})
    text = "\n".join(problems)
    assert "no snapshot 4" in text and "input_2_mute is not a member" in text and "no snapshot bank Other" in text
    _, problems = designs.snapshot_plan({"snapshots": {"error": "MissingMethodException: x"}}, {"Room_Modes": {1: {"MIC_GAIN.gain": 0}}})
    assert any("could not read" in p for p in problems)
    _, problems = designs.snapshot_plan({"components": []}, {"Room_Modes": {1: {}}})   # read by a helper from before banks
    assert any("rebuild the .NET 8 helper" in p for p in problems)


def test_matches_compares_like_qrc_reports(runtime):
    assert runtime._matches(True, {"Value": 1.0}) and runtime._matches(False, {"Value": 0.0})
    assert runtime._matches(-6, {"Value": -6.0004}) and not runtime._matches(-6, {"Value": -5.9})
    assert runtime._matches("Hall", {"Value": 0.0, "String": "Hall"}) and not runtime._matches(1, {"String": "1"})


class FakeEngine:
    """Controls plus snapshot controllers: save_N copies the bank's controls, load_N puts them back."""

    def __init__(self, values, banks):
        self.values, self.banks, self.saved = dict(values), banks, {}

    async def get(self, comp, controls):
        out = []
        for c in controls:
            v = self.values.get((comp, c), 0)
            out.append({"Name": c, "String": v if isinstance(v, str) else str(v), "Value": 0.0 if isinstance(v, str) else float(v)})
        return {"Name": comp, "Controls": out}

    async def set(self, comp, control, value, ramp=None):
        if comp in self.banks and control.split("_")[0] in ("save", "load"):
            n = int(control.split("_")[1])
            if control.startswith("save_"):
                self.saved[n] = {k: self.values.get(k, 0) for k in self.banks[comp]}
            else:
                self.values.update(self.saved.get(n, {}))
            return True
        self.values[(comp, control)] = value
        return True

    async def call(self, method, params=None):
        raise QrcError(f"{method} not expected")


class FakeDesigner:
    def __init__(self):
        self.done = []

    def stop(self):
        self.done.append("stop")
        return {"mode": "design", "dialogs": []}

    def save(self, path=None, wait_for_changes=0, timeout=30):
        self.done.append(("save", path))
        return {"saved": True}

    def close_designs(self, discard_changes=False):
        self.done.append("close")
        return []


@pytest.mark.asyncio
async def test_save_snapshots_saves_checks_and_puts_access_back(runtime, monkeypatch, tmp_path):
    original = tmp_path / "room.qsys"
    original.write_bytes(b"")
    before = {"components": [{"name": "MIXER", "script_access": "None"}, {"name": "MIC_GAIN", "script_access": "Script"},
                             {"name": "PROGRAM_EQ", "script_access": "None"}],
              "snapshots": {"banks": [READ_BANK]}}
    after = copy.deepcopy(before)
    after["snapshots"]["banks"][0]["stored"] = [1, 2]
    reads = [before, after]
    helper = []
    monkeypatch.setattr(runtime.designs, "read", lambda p: reads.pop(0))
    monkeypatch.setattr(headless, "pick", lambda *a, **kw: headless.Install(tmp_path, "9.13.2.0"))
    monkeypatch.setattr(headless, "run", lambda req, inst: helper.append(req) or {})

    async def fake_emulate(designer, qrc, path, test_copy=True, discard_changes=False):
        return {"emulating": True, "opened": str(tmp_path / "room__test.qsys")}

    async def no_wait(*a, **kw):
        return None

    monkeypatch.setattr(runtime, "emulate", fake_emulate)
    monkeypatch.setattr(runtime.asyncio, "sleep", no_wait)
    start = {("MIXER", "input_1_mute"): 0, ("MIC_GAIN", "gain"): 0, ("MIC_GAIN", "mute"): 0, ("PROGRAM_EQ", "bypass"): 0}
    engine = FakeEngine(start, {"Room_Modes": list(start)})
    dsg = FakeDesigner()

    res = await runtime.save_snapshots(dsg, engine, str(original), SPEC)

    assert res["missing_in_file"] == [] and res["stored"] == {"Room_Modes": [1, 2]}
    assert res["banks"] == [{"bank": "Room_Modes", "saved": [1, 2], "mismatched_on_load": []}]
    assert engine.saved[1][("MIXER", "input_1_mute")] == 1 and engine.saved[1][("MIC_GAIN", "gain")] == -6
    # each snapshot starts from the design's own values: snapshot 2 doesn't inherit snapshot 1's gain
    assert engine.saved[2][("MIC_GAIN", "gain")] == 0.0 and engine.saved[2][("PROGRAM_EQ", "bypass")] == 1
    assert all(engine.values[k] == v for k, v in start.items())     # the run ends on the design's values
    assert dsg.done == ["stop", ("save", str(tmp_path / "room__test.qsys")), "close"]
    assert helper == [{"command": "copy", "path": str(tmp_path / "room__test.qsys"), "out_path": str(original), "overwrite": True,
                       "script_access_map": {"MIXER": "None", "MIC_GAIN": "Script", "PROGRAM_EQ": "None", "Room_Modes": "Script"}}]


@pytest.mark.asyncio
async def test_save_snapshots_rejects_values_the_design_cannot_hold(runtime, monkeypatch, tmp_path):
    monkeypatch.setattr(runtime.designs, "read", lambda p: {"components": [], "snapshots": {"banks": [READ_BANK]}})
    monkeypatch.setattr(headless, "pick", lambda *a, **kw: headless.Install(tmp_path, "9.13.2.0"))
    with pytest.raises(designs.SpecError, match="not a member"):
        await runtime.save_snapshots(FakeDesigner(), FakeEngine({}, {}), str(tmp_path / "room.qsys"),
                                     {"Room_Modes": {"1": {"MIC_GAIN.bypass": True}}})
