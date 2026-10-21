from pathlib import Path

import pytest

from qsys_mcp import app, headless


def fake_installs(monkeypatch, versions):
    found = [headless.Install(Path(f"C:/QSC/Q-SYS Designer {v}"), v) for v in versions]
    monkeypatch.setattr(headless, "installs", lambda: sorted(found, key=lambda i: headless._vkey(i.version), reverse=True))
    monkeypatch.delenv("QSYS_DESIGNER_DIR", raising=False)


def test_pick_newest_explicit_and_by_file(monkeypatch, tmp_path):
    fake_installs(monkeypatch, ["9.13.1.0", "10.3.0.0", "9.12.0.0"])
    assert headless.pick().version == "10.3.0.0"
    assert headless.pick("9.13").version == "9.13.1.0"
    f = tmp_path / "old.qsys"
    f.write_bytes(b"\x00\x01Q-Sys Designer, Version=9.12.0.0, Culture=neutral")
    assert headless.file_designer_version(f) == "9.12.0.0"
    assert headless.pick(for_file=f).version == "9.12.0.0"
    with pytest.raises(headless.HelperError, match="not installed"):
        headless.pick("8.4")


def test_pick_with_nothing_installed(monkeypatch):
    fake_installs(monkeypatch, [])
    with pytest.raises(headless.HelperError, match="No Q-SYS Designer"):
        headless.pick()


def test_positions_cache_roundtrip(monkeypatch, tmp_path):
    monkeypatch.setenv("QSYS_MCP_CACHE", str(tmp_path))
    inst = headless.Install(tmp_path, "9.13.1.0")
    e = {"class": "gain", "control": "gain", "min": -100.0, "max": 20.0, "value": -6.0, "position": 0.7833}
    headless.save_positions(inst, [e])
    headless.save_positions(inst, [dict(e, position=0.78333)])   # same key: replaced, not duplicated
    assert headless.load_positions(inst) == [dict(e, position=0.78333)]


class FakeWindow:
    def __init__(self, title, pid):
        self.title, self.pid, self.closed = title, pid, False

    def window_text(self):
        return self.title

    def process_id(self):
        return self.pid

    def close(self):
        self.closed = True


def test_title_and_choice_helpers():
    assert app._title_unsaved("Room* - Q-SYS Designer [10.3.0]")
    assert not app._title_unsaved("Room - Q-SYS Designer [9.13.1]")
    assert app._norm("_Don't Save") == app._norm("Don\u2019t save") == "don't save"
    assert app._norm("&No") == "no"
    notice = {"title": "MessageBox", "text": ["Design License Requirements Have Changed", "OK"], "buttons": ["OK"]}
    assert app.Designer.is_license_notice(notice)
    assert not app.Designer.is_license_notice({**notice, "buttons": ["OK", "Cancel"]})


def test_close_refuses_on_unsaved_and_closes_nothing(monkeypatch):
    wins = [FakeWindow("Saved - Q-SYS Designer [9.13.1]", 1), FakeWindow("Edited* - Q-SYS Designer [9.13.1]", 2)]
    monkeypatch.setattr(app, "_designer_windows", lambda: wins)
    with pytest.raises(app.AppError, match="Edited"):
        app.Designer().close_designs()
    assert not any(w.closed for w in wins)


def test_close_all_when_clean(monkeypatch):
    wins = [FakeWindow("A - Q-SYS Designer [9.13.1]", 1), FakeWindow("B - Q-SYS Designer [10.3.0]", 2)]
    monkeypatch.setattr(app, "_designer_windows", lambda: wins)
    monkeypatch.setattr(app.Designer, "_close_instance", lambda self, w, discard: (w.close(), w.title)[1])
    assert app.Designer().close_designs() == [w.title for w in wins]
    assert all(w.closed for w in wins)


def test_open_rejects_missing_file(tmp_path):
    with pytest.raises(app.AppError, match="No .qsys file"):
        app.Designer().open_design(str(tmp_path / "missing.qsys"))


def test_scenario_rejects_steps_without_one_action():
    import asyncio
    import pytest
    from qsys_mcp import runtime
    with pytest.raises(ValueError) as e:
        asyncio.run(runtime.run_scenario(None, None, "x.qsys", [{"set": {}, "wait": 1}, {"label": "nothing"}]))
    assert "steps[0]" in str(e.value) and "steps[1]" in str(e.value)


def test_scenario_value_comparison():
    from qsys_mcp.runtime import _close_enough
    assert _close_enough(1.0, True, 1e-3) and not _close_enough(0.0, True, 1e-3)
    assert _close_enough(-6.0004, -6, 1e-3) and not _close_enough(-5.9, -6, 1e-3)
    assert _close_enough("Manual", "Manual", 0) and not _close_enough(None, 0, 1)


def test_edit_validates_ops(tmp_path):
    import pytest
    from qsys_mcp import designs
    f = tmp_path / "x.qsys"
    f.write_bytes(b"")
    with pytest.raises(designs.SpecError) as e:
        designs.edit(str(f), [{"op": "explode"}, {"op": "add_component", "component": {"name": "bad name"}},
                              {"op": "set_lua", "component": "TC", "lua": "if then"}])
    text = str(e.value)
    assert "ops[0]: op must be one of" in text and "ops[1]: the new component needs a valid name" in text
    assert "ops[2]: Lua syntax error" in text
