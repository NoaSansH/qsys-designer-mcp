import re
from pathlib import Path

import pytest

from qsys_mcp import designs, headless

HELPER = Path(designs.__file__).parent / "helper"
CSPROJ = Path(designs.__file__).parents[2] / "helper-net8" / "QsysHelper.csproj"


def hw_spec():
    return {
        "core": {"model": "Core 110f", "name": "Core-1", "set": {"SomeSwitch": True},
                 "usb": {"type": "Speakerphone", "mode": "WithEchoCancel", "video_bridge": True, "set": {"ZRMode": False}}},
        "design": {"set": {"PtpDomain": 0, "PtpPriority1": 128, "QosPreset": "Default"}},
        "inventory": [
            {"type": "IoUsbBridge", "name": "IO-USB-1",
             "usb": {"type": "sound card", "count": 2, "slots": [{"Name": "PC"}, {}]}},
            {"type": "OnVifCamera", "name": "Cam-1", "set": {"Model": "NC-12x80", "Connection": 0}},
        ],
        "components": [
            {"name": "SP_IN", "inventory": {"class": "usb_receiver"}},
            {"name": "CAM", "inventory": {"item": "Cam-1"}},
            {"name": "IO_IN", "inventory": {"item": "IO-USB-1", "placeable": "USB Sound Card 1 In"}},
            {"name": "FLEX", "inventory": "Flex In"},
        ],
    }


def problems_of(spec):
    return designs.validate(spec)[1]


def test_valid_hardware_spec_has_no_problems():
    assert problems_of(hw_spec()) == []


def _set(spec, path, value):
    *head, last = path
    for k in head:
        spec = spec[k]
    spec[last] = value


@pytest.mark.parametrize("path, value, expected", [
    (["core"], "Core 110f", "core must be an object"),
    (["core", "usb_bridge"], {}, "core: unknown key 'usb_bridge'"),
    (["core", "set"], ["SomeSwitch"], "core.set must be an object"),
    (["core", "set", "SomeSwitch"], {"on": True}, "core.set.SomeSwitch must be a number"),
    (["core", "usb"], "speakerphone", "core.usb must be an object"),
    (["core", "usb", "type"], "headset", "core.usb.type must be speakerphone, soundcard or none"),
    (["core", "usb", "count"], -1, "core.usb.count must be a whole number"),
    (["core", "usb", "count"], True, "core.usb.count must be a whole number"),
    (["core", "usb", "count"], 1.5, "core.usb.count must be a whole number"),
    (["core", "usb", "mode"], True, "core.usb.mode must be a name"),
    (["core", "usb", "video_bridge"], "yes", "core.usb.video_bridge must be true or false"),
    (["core", "usb", "speakerphone_mode"], "x", "core.usb: unknown key 'speakerphone_mode'"),
    (["core", "usb", "set"], {"ZRMode": [1]}, "core.usb.set.ZRMode must be a number"),
    (["core", "usb", "slots"], {"Name": "PC"}, "core.usb.slots must be a list"),
    (["inventory", 0, "usb", "slots"], [{"Name": "PC"}, "x"], "inventory[0].usb.slots[1] must be an object"),
    (["inventory", 0, "usb", "type"], "none", "inventory[0].usb: type none can't have count 2"),
    (["inventory", 0, "usb", "bridge"], 1, "inventory[0].usb: unknown key 'bridge'"),
    (["inventory", 1, "type"], "", "inventory[1]: needs a type"),
    (["inventory", 1, "settings"], {}, "inventory[1]: unknown key 'settings'"),
    (["inventory", 1, "set"], "Model=NC-12x80", "inventory[1].set must be an object"),
    (["inventory"], {"type": "IoUsbBridge"}, "inventory must be a list"),
    (["design"], [], "design must be an object"),
    (["design", "properties"], {}, "design: unknown key 'properties'"),
    (["design", "set", "PtpDomain"], [0], "design.set.PtpDomain must be a number"),
    (["usb_sound_cards"], 1, "give the Core's USB once"),
    (["components", 0, "inventory"], {"class": "usb_receiver", "pin": 1}, "SP_IN: unknown inventory key 'pin'"),
    (["components", 0, "inventory"], {"class": 7}, "SP_IN: inventory.class must be a name"),
    (["components", 0, "inventory"], {"placable": "X"}, "SP_IN: inventory needs an item, a placeable or a class"),
    (["components", 0, "inventory"], ["Flex In"], "SP_IN: inventory must be a placeable name"),
])
def test_hardware_problems_are_reported(path, value, expected):
    spec = hw_spec()
    _set(spec, path, value)
    problems = problems_of(spec)
    assert any(expected in p for p in problems), problems


def test_usb_type_and_count_alone_are_fine():
    for usb in ({"type": "none"}, {"type": "none", "count": 0}, {"count": 3}, {"type": "soundcard"}, {"mode": 1}):
        assert problems_of({"core": {"usb": usb}}) == [], usb


def test_uses_hardware():
    assert designs.uses_hardware(hw_spec())
    assert not designs.uses_hardware({"core": {"model": "Core 110f", "name": "Core-1"}, "usb_sound_cards": 2,
                                      "inventory": [{"type": "SoftDanteInput", "set": {"ChannelCount": "Dn8"}}],
                                      "components": [{"name": "A", "inventory": "Flex In"},
                                                     {"name": "B", "inventory": {"item": "X", "placeable": "Y"}}]})
    assert designs.uses_hardware({"design": {"set": {"MtuValue": 1500}}})
    assert designs.uses_hardware({"inventory": [{"type": "IoUsbBridge", "usb": {"type": "speakerphone"}}]})
    assert designs.uses_hardware({"components": [{"name": "C", "inventory": {"item": "Cam-1"}}]})
    assert designs.uses_hardware({"components": [{"name": "C", "inventory": {"placeable": "P", "class": "usb_uvc"}}]})


def read_back():
    return {
        "path": "x.qsys", "designer": "9.13.2.0", "saved_by": "9.13.2.0",
        "core": {"model": "Core 110f", "name": "Core-1",
                 "usb": {"api": "bridge", "type": "speakerphone", "count": 1, "mode": "WithEchoCancel", "video_bridge": True},
                 "settings": {"Name": "Core-1", "IsRedundant": False}},
        "design": {"settings": {"PtpDomain": 0, "QosPreset": "Default"}},
        "usb_slots": [{"name": "USB 1", "type": "Speakerphone", "inputs": "2", "outputs": "2"}],
        "inventory": [{"type": "Core", "name": "Core-1"},
                      {"type": "OnVifCamera", "name": "Cam-1", "settings": {"Model": "NC12x80", "Connection": 0}}],
        "pages": ["Main"],
        "components": [{"name": "G", "class": "gain", "properties": {}, "values": {}, "pins": []}],
        "wires": [],
        "ucis": [],
    }


def test_summarise_includes_settings_by_default():
    d = designs.summarise(read_back())
    assert d["core"]["settings"]["IsRedundant"] is False
    assert d["core"]["usb"]["type"] == "speakerphone"
    assert d["design"]["settings"]["PtpDomain"] == 0
    assert d["inventory"][1]["settings"]["Model"] == "NC12x80"


def test_summarise_without_settings_keeps_usb():
    src = read_back()
    d = designs.summarise(src, include={"wires"})
    assert "design" not in d
    assert "settings" not in d["core"] and d["core"]["usb"]["mode"] == "WithEchoCancel"
    assert all("settings" not in i for i in d["inventory"])
    assert d["usb_slots"][0]["type"] == "Speakerphone"
    assert "settings" in src["core"] and "settings" in src["inventory"][1]   # the read-back itself is untouched


@pytest.fixture
def net_install(monkeypatch, tmp_path):
    inst = headless.Install(tmp_path, "10.2.0.0")
    monkeypatch.setattr(headless, "is_dotnet_core", lambda i: True)
    monkeypatch.setattr(designs, "_HARDWARE_OK", set())
    return inst


def test_require_hardware_refuses_an_old_prebuilt_helper(monkeypatch, net_install):
    calls = []
    monkeypatch.setattr(headless, "run", lambda req, inst=None, timeout=300: calls.append(req["command"]) or {"core_models": []})
    with pytest.raises(headless.HelperError, match="dotnet publish helper-net8"):
        designs.require_hardware(net_install)
    assert calls == ["info"]


def test_require_hardware_accepts_a_new_helper_and_remembers(monkeypatch, net_install):
    calls = []
    monkeypatch.setattr(headless, "run", lambda req, inst=None, timeout=300: calls.append(req["command"]) or {"features": ["hardware"]})
    designs.require_hardware(net_install)
    designs.require_hardware(net_install)
    assert calls == ["info"]


def test_require_hardware_skips_framework_designers(monkeypatch, tmp_path):
    monkeypatch.setattr(headless, "is_dotnet_core", lambda i: False)
    monkeypatch.setattr(headless, "run", lambda *a, **k: pytest.fail("no helper run needed: 9.x compiles the current sources"))
    designs.require_hardware(headless.Install(tmp_path, "9.13.2.0"))


def test_build_checks_the_helper_before_building(monkeypatch, net_install, tmp_path):
    calls = []
    monkeypatch.setattr(headless, "pick", lambda version=None, for_file=None: net_install)
    monkeypatch.setattr(headless, "run", lambda req, inst=None, timeout=300: calls.append(req["command"]) or {})
    with pytest.raises(headless.HelperError, match="older than the hardware settings"):
        designs.build({"core": {"usb": {"type": "speakerphone"}}}, str(tmp_path / "new.qsys"))
    assert calls == ["info"]


def test_build_reports_hardware_problems_before_running(monkeypatch, tmp_path):
    monkeypatch.setattr(headless, "pick", lambda *a, **k: pytest.fail("a bad spec must not reach Designer"))
    with pytest.raises(designs.SpecError, match="core.usb.type"):
        designs.build({"core": {"usb": {"type": "headset"}}}, str(tmp_path / "new.qsys"))


def test_helper_sources_are_all_compiled():
    on_disk = sorted(p.name for p in HELPER.glob("*.cs"))
    assert sorted(headless.HELPER_SOURCES) == on_disk
    if CSPROJ.is_file():   # source checkout (not an installed wheel)
        compiled = re.findall(r'<Compile Include="\.\./src/qsys_mcp/helper/([^"]+)"', CSPROJ.read_text(encoding="utf-8"))
        assert sorted(compiled) == on_disk
