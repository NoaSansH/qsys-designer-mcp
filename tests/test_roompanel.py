"""The user UCI: roles found in a design, a layout without overlaps inside the page, and built on the
boardroom example."""
import json
from pathlib import Path

import pytest

from qsys_mcp import designs, headless, roompanel

EXAMPLES = Path(__file__).parent.parent / "examples"
INSTALLS = headless.installs()


def comp(name, cls, label=""):
    return {"name": name, "class": cls, "user_label": label, "pins": [], "values": {}, "properties": {}}


DESIGN = {"components": [comp("ROOM_VOL", "gain"), comp("LOBBY_VOL", "gain", "Volume\nlobby"), comp("MIC_MUTE", "gain"),
                         comp("CAM", "onvif_camera_operative"), comp("MODES", "snapshot_controller"), comp("CAM_POS", "snapshot_controller")],
          "snapshot_banks": [{"name": "Modes", "controller": "MODES", "count": 4, "saved_snapshots": [1, 2, 3]},
                             {"name": "Camera", "controller": "CAM_POS", "count": 4, "saved_snapshots": []}]}


def _overlaps(els):
    return [(a, b) for i, a in enumerate(els) for b in els[i + 1:]
            if min(a["x"] + a["w"], b["x"] + b["w"]) > max(a["x"], b["x"]) and min(a["y"] + a["h"], b["y"] + b["h"]) > max(a["y"], b["y"])]


def test_roles_found_and_labels_kept():
    r = roompanel.roles(DESIGN, {"modes": {"labels": ["Talk", "Film", "Off"]}})
    assert r["modes"] == {"labels": ["Talk", "Film", "Off"], "controller": "MODES"}
    assert r["camera"] == {"controller": "CAM_POS"} and r["volumes"] == ["ROOM_VOL", "LOBBY_VOL"]
    assert r["mic_mute"] == "MIC_MUTE" and r["privacy"] == {"component": "CAM", "control": "toggle_privacy"}


@pytest.mark.parametrize("size", [(1280, 800), (1024, 600)])
def test_layout_fits_without_overlaps(size):
    u = roompanel.build(DESIGN, "Room", *size, {"modes": {"labels": ["Talk", "Film", "Off"]}}, lang="en")
    assert [p["name"] for p in u["pages"]] == ["Home", "Audio", "Camera"] and u["navigation"] == "Left"
    for p in u["pages"]:
        assert not _overlaps(p["elements"]), p["name"]
        assert all(e["x"] + e["w"] <= size[0] and e["y"] + e["h"] <= size[1] for e in p["elements"]), p["name"]
    home = [e.get("label") for e in u["pages"][0]["elements"] if e.get("control", "").startswith("load_")]
    assert home == ["Talk", "Film", "Off"]          # only the saved snapshots


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
def test_built_on_the_boardroom(tmp_path):
    from qsys_mcp import server
    p = tmp_path / "board.qsys"
    designs.build(json.loads((EXAMPLES / "boardroom.json").read_text(encoding="utf-8")), str(p), base_dir=str(EXAMPLES))
    r = server.make_room_uci(str(p), "Room", lang="en")
    assert r["pages"] and r["elements"] > 3
    assert any(u["name"] == "Room" for u in designs.read(str(p))["ucis"])
