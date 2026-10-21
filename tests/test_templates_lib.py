"""The templates that come with the server: they expand, validate, build in every installed Designer,
pass the static checks and the audit (no mic to the speakers, no PC loop)."""
import pytest

from qsys_mcp import audit, designs, headless, templates

INSTALLS = headless.installs()
NAMES = [t["name"] for t in templates.library()]
IO = {"USB_IN", "USB_OUT", "MIC", "FLEX_IN", "SPK", "REF"}   # Core and device I/O: spare channels are expected


def test_library_lists_each_template_with_its_params():
    lib = {t["name"]: t for t in templates.library()}
    assert {"conference_usb_dante_mic", "conference_core_aec", "speaker_zone"} <= set(lib)
    for t in lib.values():
        assert t["description"] and t["components"]
        assert set(t["param_help"]) <= set(t["params"])


@pytest.mark.parametrize("name", NAMES)
def test_template_expands_and_validates(name):
    spec = templates.expand({"core": {"model": "Core8flex"}, "include": [{"template": name}]})
    assert not designs.validate(spec)[1]
    assert all("${" not in str(c) for c in spec["components"])


def test_shared_block_is_placed_once():
    spec = templates.expand({"include": [{"template": "speaker_zone", "prefix": "Z1_"},
                                         {"template": "speaker_zone", "prefix": "Z2_", "params": {"SPK_L": 3, "SPK_R": 4}}]})
    spk = [c for c in spec["components"] if c["name"] == "SPK"]
    assert len(spk) == 1 and spk[0]["values"]["channel_3_flex_output_enable"] is True
    assert ["Z2_LIM.out.1", "SPK.in.3", "Z2_Spk L"] in spec["wires"]
    assert not designs.validate(spec)[1]


def test_same_core_block_twice_is_refused():
    spec = {"components": [{"name": "A", "inventory": "Flex Out"}, {"name": "B", "inventory": "Flex Out"}]}
    assert any("already placed as A" in p for p in designs.validate(spec)[1])


def test_unknown_template_lists_the_library():
    with pytest.raises(designs.SpecError, match="speaker_zone"):
        templates.expand({"include": [{"template": "nope"}]})


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
@pytest.mark.parametrize("name", NAMES)
def test_template_builds_checks_and_audits(name, inst, tmp_path):
    out = tmp_path / f"{name}.qsys"
    built = designs.build({"core": {"model": "Core8flex"}, "layout": "auto", "include": [{"template": name}]},
                          str(out), designer_version=inst.version)
    assert not built["warnings"]
    d = designs.read(str(out))
    loose_ok = [c["name"] for c in d["components"] if c["name"] in IO or c["class"] == "router_with_output"]
    assert all(r["pass"] for r in designs.check(d, allow_unconnected=loose_ok)["results"])
    a = audit.audit(d)
    assert a["errors"] == 0 and a["warnings"] == 0, a["findings"]


def test_divisible_room_wall_script_follows_the_prefix():
    from qsys_mcp import luatest
    try:
        luatest.find_lua()
    except FileNotFoundError:
        pytest.skip("no Lua interpreter")
    spec = templates.expand({"include": [{"template": "divisible_room", "prefix": "R1_", "params": {"PREFIX": "R1_"}}]})
    wall = next(c for c in spec["components"] if c["name"] == "R1_WALL")
    r = luatest.run(wall["text_controller"]["lua"], wall["text_controller"]["controls"], [
        {"expect": {"control": "State", "string": "Separated"}},
        {"expect": {"component": "R1_ROUTE", "control": "input_1_output_2_mute", "value": True}},
        {"set": {"control": "WallOpen", "value": True}},
        {"expect": {"component": "R1_ROUTE", "control": "input_1_output_2_mute", "value": False}},
        {"expect": {"component": "R1_ROUTE", "control": "input_2_output_1_mute", "value": False}},
        {"expect": {"control": "State", "string": "Combined"}}])
    assert r["ok"], r["failures"]
