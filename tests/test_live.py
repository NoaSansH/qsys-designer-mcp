"""compare and pull_snapshots against a fake Core that answers QRC like the engine does."""
import asyncio

import pytest

from qsys_mcp import live
from qsys_mcp.qrc.client import QrcError


class FakeCore:
    host = "10.0.0.2"

    def __init__(self):
        self.state = {"VOL": {"gain": -10.0, "mute": False}, "TC": {"Notes": "now"}}
        self.snaps = {1: {("VOL", "gain"): -3.0, ("VOL", "mute"): True, ("TC", "Notes"): "one"}}
        self.matched = None

    def _row(self, comp, name):
        v = self.state[comp][name]
        if isinstance(v, str):
            return {"Name": name, "Type": "Text", "String": v, "Value": 0.0}
        return {"Name": name, "Type": "Boolean" if isinstance(v, bool) else "Float", "Value": v, "String": str(v)}

    async def status(self):
        return {"DesignName": "room", "DesignCode": "abc", "Platform": "Core 110f"}

    async def controls(self, comp):
        if comp == "HIDDEN":
            raise QrcError("unknown component")
        if comp == "MODES":
            return {"Controls": [{"Name": "load.1"}, {"Name": "match.1"}]}
        return {"Controls": [self._row(comp, n) for n in self.state[comp]]}

    async def get(self, comp, names):
        if comp == "MODES":
            return {"Controls": [{"Name": n, "Value": self.matched == int(n.split("_")[1])} for n in names]}
        return {"Controls": [{k: v for k, v in self._row(comp, n).items() if k != "Type"} for n in names]}

    async def set(self, comp, name, value):
        if comp == "MODES":
            n = int(name.split("_")[1])
            self.matched = n if n in self.snaps else None
            for (c, k), v in self.snaps.get(n, {}).items():
                self.state[c][k] = v
        else:
            old = self.state[comp][name]
            self.state[comp][name] = value if isinstance(old, str) else type(old)(value)


DESIGN = {"path": r"C:\x\room.qsys",
          "components": [{"name": "VOL", "values": {"gain": {"value": -10.0}, "mute": {"value": 1.0}}},
                         {"name": "TC", "values": {"Notes": {"string": "now"}, "code": {"string": "-- lua"}}},
                         {"name": "HIDDEN", "values": {"gain": {"value": 0.0}}}],
          "snapshot_banks": [{"name": "MODES", "count": 2, "controller": "MODES",
                              "controls": [{"component": "VOL", "all": False, "controls": ["gain", "mute"]},
                                           {"component": "TC", "all": False, "controls": ["Notes"]},
                                           {"component": "HIDDEN", "all": False, "controls": ["gain"]}]}]}


def test_compare_reports_differences_and_hidden_blocks():
    r = asyncio.run(live.compare(FakeCore(), DESIGN))
    assert r["running_design"] == "room" and r["same_design_name"] and r["checked"] == 3
    assert r["differences"] == [{"component": "VOL", "control": "mute", "file": 1.0, "core": 0, "core_string": "False"}]
    assert r["not_visible_over_qrc"] == ["HIDDEN"] and not r["same"]


def test_pull_reads_each_snapshot_and_puts_the_room_back():
    core = FakeCore()
    r = asyncio.run(live.pull_snapshots(core, DESIGN, settle=0))["MODES"]
    assert r["snapshots"] == {"1": [{"component": "TC", "control": "Notes", "value": "one"},
                                    {"component": "VOL", "control": "gain", "value": -3},
                                    {"component": "VOL", "control": "mute", "value": 1}]}
    assert r["empty"] == [2] and r["not_visible"] == ["HIDDEN"]
    assert core.state == {"VOL": {"gain": -10.0, "mute": False}, "TC": {"Notes": "now"}}


def test_a_bank_without_controller_cannot_be_pulled():
    d = {**DESIGN, "snapshot_banks": [{**DESIGN["snapshot_banks"][0], "controller": None}]}
    with pytest.raises(ValueError, match="no Snapshot Controller"):
        asyncio.run(live.pull_snapshots(FakeCore(), d))

