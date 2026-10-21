from pathlib import Path

from qsys_mcp import simpl

DATA = Path(__file__).parent / "data" / "simpl"


def test_program_modules_give_blocks_and_controls():
    c = simpl.contract(DATA / "demo.smw")
    comps = c["components"]
    assert comps["ROOM_VOL"]["controls"] == [{"id": "gain", "write": True}, {"id": "mute", "write": True}]
    # the camera: only the wired privacy input (the tilt input sits on a reserved signal) and its feedback
    assert comps["ROOM_CAM"]["controls"] == [{"id": "toggle_privacy", "write": True}]
    assert comps["LOGIC"]["controls"] == ["Busy"]
    assert comps["SOMETHING"]["controls"] == []
    assert c["not_checked"][0]["component"] == "SOMETHING"


def test_parameters_follow_the_usp_declarations():
    inst = {i["comment"]: i for i in simpl.read_program(DATA / "demo.smw")}
    vol = inst["Room volume"]
    assert vol["params"] == {"CoreID": "1", "ComponentName": "ROOM_VOL", "VolumeStep": "655"}
    assert vol["inputs"] == {"VolumeUp": "Room_Vol_Up"}
    assert inst["Room camera"]["outputs"] == {"PrivacyIsOn": "Cam_Privacy_Fb"}


def test_extra_module_descriptions():
    c = simpl.contract(DATA / "demo.smw", modules={"Vendor Thing": {"controls": ["status"]}})
    assert c["components"]["SOMETHING"]["controls"] == ["status"] and not c["not_checked"]
