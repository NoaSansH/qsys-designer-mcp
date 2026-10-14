from qsys_mcp.lua import PLUGIN_TEMPLATE, check_syntax, scaffold_plugin


def test_syntax_error_reported():
    res = check_syntax("Controls.Gain.EventHandler = function(c\n  print(c.Value)\nend")
    assert res["ok"] is False and res["error"]


def test_implicit_global_warning():
    res = check_syntax("count = 0\nlocal x = 1\nControls.A.Value = x")
    assert res["ok"]
    assert any("count" in w for w in res["warnings"])
    assert not any("Controls" in w for w in res["warnings"])


def test_plugin_template_parses(tmp_path):
    path = scaffold_plugin("My Device", author="me", out_dir=tmp_path)
    res = check_syntax(path.read_text(encoding="utf-8"))
    assert res["ok"], res
    assert path.name == "My_Device.qplug"
