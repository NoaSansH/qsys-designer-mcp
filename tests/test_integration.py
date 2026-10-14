"""Builds, reads back and checks the boardroom example with every installed Designer version.
Skipped when Designer isn't installed (e.g. on CI). Nothing outside pytest's tmp_path is written."""
import json
from pathlib import Path

import pytest

from qsys_mcp import designs, headless

EXAMPLES = Path(__file__).parent.parent / "examples"
INSTALLS = headless.installs()


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
def test_boardroom_build_read_check(inst, tmp_path):
    spec = json.loads((EXAMPLES / "boardroom.json").read_text(encoding="utf-8"))
    rules = json.loads((EXAMPLES / "boardroom_checks.json").read_text(encoding="utf-8"))
    out = tmp_path / "boardroom.qsys"
    built = designs.build(spec, str(out), designer_version=inst.version, base_dir=str(EXAMPLES))
    assert built["components"] == 30 and built["wires"] == 51 and built["uci_elements"] == 41
    assert not built["warnings"] and not built["unconfirmed_values"]

    d = designs.read(str(out))
    assert d["designer"] == inst.version and d["saved_by"] == inst.version
    assert d["core"]["model"] == "Core 8 Flex"
    names = {c["name"]: c for c in d["components"]}
    assert names["AEC_01"]["class"] == "acoustic_echo_canceler_simd"
    assert names["ROOM_MODE"]["script_access"] == "External" and names["SPK_EQ"]["script_access"] == "None"
    assert names["FLEX_IN"]["values"]["channel_1_preamp_gain"]["position"] == pytest.approx(36 / 64)

    result = designs.check(d, rules["paths"], rules["allow_unconnected"])
    assert result["failed"] == 0, [r for r in result["results"] if not r["pass"]]


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
def test_probe_unknown_class_is_reported_not_fatal():
    out = headless.run({"command": "probe", "classes": [{"class": "gain"}, {"class": "no_such_block"}], "placeables": False})
    assert "pins" in out["classes"][0]
    assert "unknown device class" in out["classes"][1]["error"]
