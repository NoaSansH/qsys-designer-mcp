"""Dante subscriptions and AES67 streams set in the spec: stored in the design, read back, and the
audit's warnings when a channel in use has none."""
import pytest

from qsys_mcp import audit, designs, headless

INSTALLS = headless.installs()
SPEC = {"inventory": [{"type": "SoftDanteInput", "name": "Mic-RX", "set": {"ChannelCount": "Dn8"}},
                      {"type": "Aes67Receiver", "name": "Table-RX", "set": {"ChannelCount": 2}}],
        "components": [{"name": "MIC", "inventory": {"item": "Mic-RX", "placeable": "Status/Control"}, "x": 0, "y": 0,
                        "dante_subscriptions": {"1": "MXA920-123abc@Automix Out"}},
                       {"name": "TABLE", "inventory": {"item": "Table-RX", "placeable": "Status/Control"}, "x": 0, "y": 200},
                       {"name": "MIX", "class": "mixer", "x": 300, "y": 0, "properties": {"n_inputs": 4, "n_outputs": 1}}],
        "wires": [["MIC.out.1", "MIX.in.1"], ["MIC.out.2", "MIX.in.2"], ["TABLE.out.1", "MIX.in.3"]]}


def test_sugar_becomes_values_and_bad_targets_are_reported():
    s, problems = designs.validate({"components": [{"name": "A", "class": "gain", "dante_subscriptions": {"1": "Dev@Ch", "x": "nope"},
                                                    "aes67_stream": "Room"}]})
    assert s["components"][0]["values"] == {"channel_1_subscription_device": "Dev", "channel_1_subscription_channel": "Ch",
                                           "stream_name": "Room"}
    assert any("'x'" in p for p in problems)


@pytest.mark.skipif(not INSTALLS, reason="Q-SYS Designer is not installed")
def test_stored_and_audited(tmp_path):
    p = tmp_path / "net.qsys"
    designs.build(SPEC, str(p))
    d = designs.read(str(p))
    mic = next(c for c in d["components"] if c["name"] == "MIC")
    assert mic["values"]["channel_1_subscription_device"]["string"] == "MXA920-123abc"
    assert mic["values"]["channel_1_subscription_channel"]["string"] == "Automix Out"
    found = {(f["rule"], f["detail"].split(":")[0]) for f in audit.audit(d)["findings"] if f["severity"] == "warning"}
    assert ("Dante channel in use with no subscription", "MIC channel 2") in found
    assert ("AES67 receiver with no stream", "TABLE") in found
    assert not any(d == "MIC channel 1" for _, d in found)
