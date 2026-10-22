"""export: the boardroom example, built, exported and built again from the export, is the same design
(blocks, properties, values, positions, wires, signal names, banks, UCIs element by element)."""
import json
from pathlib import Path

import pytest

from qsys_mcp import designs, export, headless, report

EXAMPLES = Path(__file__).parent.parent / "examples"
INSTALLS = headless.installs()


def _uci_elements(design):
    def one(e):
        return tuple(sorted((k, round(v, 2) if isinstance(v, float) else str(v)) for k, v in e.items() if k not in ("z", "data")))
    return [sorted(one(e) for e in p["elements"]) for u in design["ucis"] for p in u["pages"]]


def test_properties_text_becomes_json_values():
    assert [export._scalar(x) for x in ("True", "8", "0,005", "-1.5E-3", "Mix Only")] == [True, 8, 0.005, -0.0015, "Mix Only"]


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
def test_boardroom_round_trip(inst, tmp_path):
    spec = json.loads((EXAMPLES / "boardroom.json").read_text(encoding="utf-8"))
    first = tmp_path / "first.qsys"
    designs.build(spec, str(first), designer_version=inst.version, base_dir=str(EXAMPLES))
    out = export.export(str(first))
    assert not out["notes"]
    json.dumps(out)                                   # plain JSON, ready to save and edit
    second = tmp_path / "second.qsys"
    built = designs.build(out["spec"], str(second), designer_version=inst.version)
    assert not built["warnings"]
    a, b = designs.read(str(first)), designs.read(str(second))
    diff = report.diff(a, b, positions=True)
    assert diff["same"], {k: v for k, v in diff.items() if v}
    assert _uci_elements(a) == _uci_elements(b)
