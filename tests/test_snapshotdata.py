"""Snapshot files read without Designer: two snapshots saved by the engine in emulation (a gain, a
mixer crosspoint, a router, a Text Controller's knob, toggle and text) and the GUIDs of their blocks."""
import json
from pathlib import Path

import pytest

from qsys_mcp import report, snapshotdata

DATA = Path(__file__).parent / "data" / "snapshots"
BLOCKS = json.loads((DATA / "lab_blocks.json").read_text(encoding="utf-8"))


def named(n):
    names = {snapshotdata.engine_id(g): b for b, g in BLOCKS.items()}
    return {f"{names[i]}.{c}": v for i, c, v in snapshotdata.parse((DATA / f"lab_{n}.bin").read_bytes())}


def test_engine_id_is_the_ascii85_of_the_guid():
    assert snapshotdata.engine_id(BLOCKS["G"]) == "C<u']F`2.nWkWE2Jqbs!"


def test_numbers_and_text_are_read():
    assert named(1) == {"R.select_1": 3, "MX.input_1_output_2_gain": -3, "TC.Flag": 1, "TC.Lvl": 7, "G.mute": 1,
                        "MX.input_2_output_1_mute": 0, "G.gain": -12.5, "TC.Notes": "Bonjour"}
    two = named(2)
    assert two["MX.input_1_output_2_gain"] == -100 and two["G.gain"] == 6
    assert two["TC.Notes"] == "Un texte plus long, avec accents : éà"


def test_not_a_snapshot_is_refused():
    with pytest.raises(snapshotdata.SnapshotFormatError):
        snapshotdata.parse(b"x" * 40)
    raw = bytearray((DATA / "lab_1.bin").read_bytes())
    raw[3] ^= 0xFF                      # inside the format number
    with pytest.raises(snapshotdata.SnapshotFormatError):
        snapshotdata.parse(bytes(raw))


def test_compare_reports_snapshot_values():
    a = {"components": [], "wires": [], "snapshot_banks": [{"name": "LAB", "saved_snapshots": [1]}]}
    d = report.diff(a, a, snapshots_a={"LAB": {"1": named(1)}}, snapshots_b={"LAB": {"1": {**named(1), "G.gain": -6}}})
    assert d["snapshot_values_changed"] == {"LAB": {"1": {"G.gain": [-12.5, -6]}}} and not d["same"]
    assert report.diff(a, a, snapshots_a={}, snapshots_b={})["same"]
