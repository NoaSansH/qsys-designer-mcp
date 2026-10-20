from pathlib import Path

from qsys_mcp import designs, headless

ROOT = Path(__file__).parent.parent


def uci(*elements, **size):
    return {"name": "Panel", **size, "pages": [{"name": "Main", "elements": list(elements)}]}


def spec(*ucis):
    return {"components": [{"name": "LEVEL", "class": "gain"}], "ucis": list(ucis)}


def test_validate_accepts_size_and_graphics():
    s = spec(uci({"type": "group_box", "text": "AUDIO", "x": 20, "y": 20, "w": 600, "h": 400, "font_size": 14, "bold": True,
                  "color": "#FFFFFFFF", "fill": "#40000000", "stroke": "#5A5B60", "stroke_width": 1, "corner_radius": 8,
                  "align": "Left"},
                 {"type": "header", "text": "LEVEL", "x": 40, "y": 60, "w": 560, "h": 24.5, "font_size": 16, "bold": False,
                  "color": "#FFD6D9DE", "align": "center"},
                 {"type": "text", "text": "dB", "x": 150, "y": 100},
                 {"type": "control", "component": "LEVEL", "control": "gain", "x": 60, "y": 100, "w": 80, "h": 280},
                 width=1280, height=800),
             uci({"type": "header"}))   # no size: Designer's default
    assert designs.validate(s)[1] == []


def test_validate_reports_uci_problems():
    s = spec(uci(width=1280),
             uci({"type": "frame"},
                 {"type": "header", "fill": "#FF000000", "stroke_width": 2},
                 {"type": "group_box", "color": "white", "stroke_width": 1.5, "bold": "yes", "align": "Middle", "w": 0,
                  "corner_radius": -1, "x": "10", "text": 5},
                 {"type": "control", "component": "MISSING", "control": "gain"},
                 width=64, height=800.0))
    text = "\n".join(designs.validate(s)[1])
    for expected in ("give both width and height, or neither", "width must be a whole number of pixels from 100 to 8192, not 64",
                     "height must be a whole number of pixels from 100 to 8192, not 800.0",
                     "type must be control, text, group_box or header, not 'frame'",
                     "a header has no fill, stroke_width (a group_box does)", "color must be a colour like #AARRGGBB, not 'white'",
                     "stroke_width must be a whole number of pixels, not 1.5", "bold must be true or false", "align must be Left, Center or Right",
                     "w must be more than zero", "corner_radius must be zero or more", "x must be a number", "text must be a string",
                     "UCI Panel/Main element 3: no component MISSING"):
        assert expected in text, expected
    assert "stroke_width must be" not in text.split("a header has no")[0]   # a header's frame-only keys are reported once


def test_summarise_outlines_ucis_unless_asked_for():
    full = {"name": "Panel", "width": 1280, "height": 800, "aspect_ratio": "SixteenTen",
            "pages": [{"name": "Main", "elements": [{"type": "GroupBox", "z": 1}, {"type": "ControlStyle", "z": 2}]},
                      {"name": "Audio", "elements": []}]}
    design = {"components": [], "wires": [], "ucis": [full]}
    assert designs.summarise(design)["ucis"] == [full]
    assert designs.summarise(design, include={"wires"})["ucis"] == [
        {"name": "Panel", "width": 1280, "height": 800, "pages": ["Main", "Audio"], "elements": 2}]


def test_helper_sources_are_packaged_and_in_the_net8_project():
    sources = headless._sources()
    assert set(sources) == set(headless.HELPER_SOURCES) and "static class QUci" in sources["QUci.cs"]
    csproj = (ROOT / "helper-net8" / "QsysHelper.csproj").read_text(encoding="utf-8")
    for name in headless.HELPER_SOURCES:
        assert f'<Compile Include="../src/qsys_mcp/helper/{name}" />' in csproj
