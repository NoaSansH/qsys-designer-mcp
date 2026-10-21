"""Pictures of the boardroom example's schematic and UCI, drawn without Designer."""
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from qsys_mcp import designs, headless, preview

EXAMPLES = Path(__file__).parent.parent / "examples"
INSTALLS = headless.installs()


def test_colors_and_wrapping():
    assert preview._rgba("#40D32F2F") == (0xD3, 0x2F, 0x2F, 0x40) and preview._rgba("#112233") == (0x11, 0x22, 0x33, 255)
    assert preview._rgba("nonsense", (1, 2, 3, 4)) == (1, 2, 3, 4)
    img = Image.new("RGB", (10, 10))
    from PIL import ImageDraw
    lines = preview._wrap(ImageDraw.Draw(img), "one two three four five six", preview._font(12), 60)
    assert len(lines) > 1 and " ".join(lines) == "one two three four five six"


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
def test_boardroom_schematic_and_uci_pictures(tmp_path):
    spec = json.loads((EXAMPLES / "boardroom.json").read_text(encoding="utf-8"))
    out = tmp_path / "boardroom.qsys"
    designs.build(spec, str(out), base_dir=str(EXAMPLES))
    d = designs.read(str(out))
    for data in (preview.schematic(d), preview.uci(d)):
        img = Image.open(io.BytesIO(data))
        assert img.format == "PNG" and img.width > 600 and img.height > 300
    with pytest.raises(ValueError, match="no page"):
        preview.schematic(d, "nope")
    with pytest.raises(ValueError, match="no UCI"):
        preview.uci(d, "nope")
