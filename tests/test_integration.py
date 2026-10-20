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


def plugin_info(inst):
    """The helper's info for an install, with its plugin list (skips if this helper predates plugins)."""
    info = headless.run({"command": "info"}, inst)
    if "plugins_error" in info:
        pytest.fail(info["plugins_error"])
    if "plugins" not in info:
        pytest.skip("this helper doesn't list plugins (rebuild the .NET 8 helper)")
    return info


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
def test_missing_plugin_is_a_clear_error(inst, tmp_path):
    plugin_info(inst)
    out = tmp_path / "missing.qsys"
    with pytest.raises(headless.HelperError, match="no installed plugin matches 'No Such Plugin 0000'"):
        designs.build({"components": [{"name": "DEV_1", "plugin": "No Such Plugin 0000"}]}, str(out), designer_version=inst.version)
    assert not out.exists()


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
def test_plugin_probe_build_read(inst, tmp_path):
    """Places the first installed plugin Designer lists, by id, and reads it back."""
    plugins = [p for p in plugin_info(inst)["plugins"] if p.get("id") and p.get("loaded", True)]
    if not plugins:
        pytest.skip("no plugins are installed")
    p = plugins[0]
    probe = headless.run({"command": "probe", "classes": [], "placeables": False, "plugins": [{"plugin": {"id": p["id"]}}]}, inst)
    probed = probe["plugins"][0]
    assert "error" not in probed, probed
    assert probed["class"] == "%PLUGIN%_" + p["id"] and probed["installed"]["id"] == p["id"]

    out = tmp_path / "plugin.qsys"
    built = designs.build({"components": [{"name": "DEV_1", "plugin": {"id": p["id"]}, "x": 100, "y": 100}]}, str(out),
                          designer_version=inst.version)
    assert built["components"] == 1
    assert not [w for w in built["warnings"] if "plugin_source" in w or "IsPlugin" in w], built["warnings"]

    d = designs.read(str(out))
    c = d["components"][0]
    assert c["class"] == "%PLUGIN%_" + p["id"] and c["plugin"]["id"] == p["id"]
    assert c["properties"]["plugin_source"].endswith("characters, not shown)")
    assert designs.summarise(d)["plugins"][0]["components"] == ["DEV_1"]
