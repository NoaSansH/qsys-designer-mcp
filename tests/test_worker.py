"""The serving helper: one process per Designer version answers requests in turn, is restarted if it
dies, and gives the same answers as a helper run once per request."""
import pytest

from qsys_mcp import headless

INSTALLS = headless.installs()
pytestmark = pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")


@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
def test_one_process_serves_requests_and_restarts_when_killed(inst, monkeypatch):
    monkeypatch.setenv("QSYS_MCP_PERSISTENT", "1")
    first = headless.run({"command": "info"}, inst)
    w = headless._workers[inst.version]
    served = w.served
    assert headless.run({"command": "info"}, inst) == first and w.served == served + 1
    w.proc.kill()
    w.proc.wait()
    assert headless.run({"command": "info"}, inst) == first
    assert headless._workers[inst.version] is not w and headless._workers[inst.version].alive()


@pytest.mark.parametrize("inst", INSTALLS, ids=[i.version for i in INSTALLS])
def test_errors_come_back_the_same_either_way(inst, monkeypatch):
    request = {"command": "probe", "classes": [{"class": "no_such_class"}], "controls": False}
    monkeypatch.setenv("QSYS_MCP_PERSISTENT", "1")
    served = headless.run(request, inst)
    monkeypatch.setenv("QSYS_MCP_PERSISTENT", "0")
    once = headless.run(request, inst)
    assert served["classes"][0]["error"] == once["classes"][0]["error"]
    with pytest.raises(headless.HelperError, match="unknown command"):
        headless.run({"command": "nope"}, inst)
