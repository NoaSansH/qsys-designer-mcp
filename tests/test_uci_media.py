"""UCI navigation, images, page links and picture buttons: validated, built in every installed
Designer, read back and drawn by the preview."""
import copy
import io
from pathlib import Path

import pytest
from PIL import Image

from qsys_mcp import designs, headless, preview

DATA = Path(__file__).parent / "data" / "uci"
INSTALLS = headless.installs()
SPEC = {"components": [{"name": "VOL", "class": "gain", "x": 0, "y": 0}],
        "ucis": [{"name": "Room", "width": 1280, "height": 800, "navigation": "Left", "tab_style": "Chevron", "navigation_size": 180,
                  "pages": [
                      {"name": "Home", "elements": [
                          {"type": "image", "file": "logo.png", "x": 200, "y": 20, "w": 120, "h": 40},
                          {"type": "image", "file": "mark.svg", "x": 340, "y": 20, "w": 40, "h": 40},
                          {"type": "page_link", "page": "Audio", "text": "Audio", "x": 200, "y": 100, "w": 200, "h": 80},
                          {"type": "control", "component": "VOL", "control": "mute", "x": 420, "y": 100, "w": 80, "h": 40,
                           "image_on": "btn_on.png", "image_off": "btn_off.png"}]},
                      {"name": "Audio", "elements": [
                          {"type": "control", "component": "VOL", "control": "gain", "style": "Fader", "x": 200, "y": 20, "w": 60, "h": 300},
                          {"type": "page_link", "page": "Home", "text": "Back", "x": 300, "y": 20, "w": 120, "h": 60}]}]}]}


def test_files_become_image_data_and_mistakes_are_reported():
    s, problems = designs.validate(copy.deepcopy(SPEC), DATA)
    assert not problems
    els = s["ucis"][0]["pages"][0]["elements"]
    assert els[0]["format"] == "png" and "file" not in els[0] and els[1]["format"] == "svg"
    assert els[3]["image_on"]["format"] == "png" and els[3]["image_off"]["data"]
    bad = copy.deepcopy(SPEC)
    bad["ucis"][0]["navigation"] = "Middle"
    bad["ucis"][0]["pages"][0]["elements"] += [{"type": "page_link", "page": "Nowhere"}, {"type": "image", "file": "missing.png"},
                                              {"type": "image", "file": "notes.txt"}]
    problems = designs.validate(bad, DATA)[1]
    assert any("navigation must be one of" in p for p in problems)
    assert any("'Nowhere'" in p for p in problems)
    assert any("cannot read" in p for p in problems) and any("images can be" in p for p in problems)


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
def test_built_read_back_and_previewed(inst, tmp_path):
    out = tmp_path / "room.qsys"
    built = designs.build(copy.deepcopy(SPEC), str(out), designer_version=inst.version, base_dir=str(DATA))
    assert not built["warnings"] and built["uci_elements"] == 6
    u = designs.read(str(out))["ucis"][0]
    assert u["navigation"] == "Left" and u["tab_style"] == "Chevron" and u["navigation_size"] == 180
    home, audio = u["pages"]
    kinds = [(e["type"], e.get("format"), e.get("page")) for e in home["elements"]]
    assert kinds[:3] == [("GraphicImage", "image", None), ("GraphicImage", "svg", None), ("Region", None, "Audio")]
    link = home["elements"][2]
    assert (link["x"], link["y"], link["w"], link["h"]) == (200, 100, 200, 80)
    assert home["elements"][3]["images"] is True and audio["elements"][1]["page"] == "Home"
    img = Image.open(io.BytesIO(preview.uci({"ucis": [u]})))
    assert img.size == (1920, 1200)
