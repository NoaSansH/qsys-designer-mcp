"""The Lua bench's simulated devices: TCP and UDP sockets, HTTP, JSON, System and friends, and a
plugin run as Designer runs it. Skipped without a Lua interpreter."""
from pathlib import Path

import pytest

from qsys_mcp import luatest, server

try:
    luatest.find_lua()
    HAVE_LUA = True
except FileNotFoundError:
    HAVE_LUA = False
pytestmark = pytest.mark.skipif(not HAVE_LUA, reason="no Lua interpreter")
PLUGIN = Path(__file__).parent / "data" / "plugins" / "projector_demo.qplug"

TCP = r'''
local sock = TcpSocket.New()
sock.Connected = function() Controls.Online.Boolean = true; sock:Write("HELLO\r\n") end
sock.Data = function(s)
  local line = s:ReadLine(TcpSocket.EOL.CrLf)
  while line do Controls.Last.String = line; line = s:ReadLine(TcpSocket.EOL.CrLf) end
end
sock.Closed = function() Controls.Online.Boolean = false end
Controls.Volume.EventHandler = function(c) sock:Write(string.format("VOL %d\r\n", c.Value)) end
sock:Connect("10.0.0.5", 23)
'''


def test_tcp_connect_write_read_close():
    r = luatest.run(TCP, [{"name": "Online", "type": "led"}, {"name": "Last", "type": "status_text"},
                          {"name": "Volume", "type": "knob", "min": 0, "max": 100}], [
        {"expect": {"connected": True, "to": "10.0.0.5:23"}},
        {"expect": {"control": "Online", "value": True}},
        {"expect": {"sent": "HELLO\r\n", "to": "10.0.0.5:23"}},
        {"set": {"control": "Volume", "value": 40}},
        {"expect": {"sent": "VOL 40\r\n", "to": "10.0.0.5:23"}},
        {"socket": {"to": "10.0.0.5:23", "data": "VOL=40\r\nMUTE=0\r\n"}},
        {"expect": {"control": "Last", "string": "MUTE=0"}},
        {"socket": {"to": "10.0.0.5:23", "event": "Closed"}},
        {"expect": {"control": "Online", "value": False}},
        {"expect": {"sent": "nothing like this", "to": "10.0.0.5:23"}, "label": "must fail"}])
    assert r["checks"] == 7 and r["passed"] == 6 and r["failures"][0]["label"] == "must fail"
    assert r["sent"]["10.0.0.5:23"] == r"HELLO\13\10VOL 40\13\10"


def test_http_json_system_notifications_uci_crypto():
    code = r'''
local json = require("rapidjson")
HttpClient.Upload({ Url = HttpClient.CreateUrl({ Host = "http://10.0.0.7", Path = "api/login", Query = { user = "a b" } }),
  Method = "POST", Data = json.encode({ pin = 1234 }),
  EventHandler = function(t, code, data) Controls.Code.Value = code; Controls.Token.String = json.decode(data).token end })
HttpClient.Download({ Url = "http://10.0.0.7/missing", EventHandler = function(t, code, data, err) Controls.Err.String = err end })
Controls.Emu.Boolean = System.IsEmulating
Notifications.Subscribe("Combine", function(name, data) Controls.Combined.Boolean = data.on end)
Controls.Go.EventHandler = function() Uci.SetPage("Panel", "Home") end
Controls.B64.String = Crypto.Base64Encode("Qsys!") .. "|" .. Crypto.Base64Decode("UXN5cyE=")
local back = json.decode(json.encode({ list = { 1, 2.5, "é\n\"x\"" }, nested = { ok = true } }))
Controls.Round.String = back.list[3] .. tostring(back.nested.ok) .. back.list[2]
'''
    ctl = [{"name": n} for n in ("Code", "Token", "Err", "Emu", "Combined", "Go", "B64", "Round")]
    ctl[0]["max"] = 1000
    r = luatest.run(code, ctl, [
        {"expect": {"request": "POST http://10.0.0.7/api/login?user=a%20b", "body": '"pin":1234'}},
        {"expect": {"control": "Code", "value": 201}},
        {"expect": {"control": "Token", "string": "abc"}},
        {"expect": {"control": "Err", "string": "no simulated response for GET http://10.0.0.7/missing"}},
        {"expect": {"control": "Emu", "value": False}},
        {"notify": {"name": "Combine", "data": {"on": True}}},
        {"expect": {"control": "Combined", "value": True}},
        {"set": {"control": "Go", "value": 1}},
        {"expect": {"uci": "Panel", "page": "Home"}},
        {"expect": {"control": "B64", "string": "UXN5cyE=|Qsys!"}},
        {"expect": {"control": "Round", "string": "é\n\"x\"true2.5"}}],
        http=[{"url": "/api/login", "method": "POST", "code": 201, "data": {"token": "abc"}}], system={"IsEmulating": False})
    assert r["ok"], r["failures"]


def test_udp_packet_reaches_the_script():
    code = r'''
local u = UdpSocket.New()
u.EventHandler = function(s, packet) Controls.Got.String = packet.Data .. "@" .. packet.Address; s:Send(packet.Address, 5001, "ACK") end
u:Open("0.0.0.0", 5000)
'''
    r = luatest.run(code, [{"name": "Got"}], [
        {"udp": {"data": "PING", "address": "10.0.0.9", "port": 5000}},
        {"expect": {"control": "Got", "string": "PING@10.0.0.9"}},
        {"expect": {"sent": "ACK", "to": "10.0.0.9:5001"}}])
    assert r["ok"], r["failures"]


def test_plugin_runs_design_time_then_runtime():
    r = server.lua_test(plugin_file=str(PLUGIN), properties={"Inputs": 3}, steps=[
        {"set": {"control": "IP Address", "value": "10.0.0.20"}},
        {"expect": {"connected": True, "to": "10.0.0.20:4352"}},
        {"expect": {"sent": "PWR?\r", "to": "10.0.0.20:4352"}},
        {"set": {"control": "Input", "index": 3, "value": 1}},
        {"expect": {"sent": "INPUT 3\r"}},
        {"socket": {"to": "10.0.0.20:4352", "data": "PWR=ON\r"}},
        {"expect": {"control": "Power", "value": True}},
        {"advance": 5},
        {"expect": {"sent": "PWR?\r"}, "label": "polled"},
        {"expect": {"control": "Lamp Hours", "string": "1520"}}],
        http=[{"url": "/api/status", "data": {"lamp": {"hours": 1520}}}])
    assert r["ok"], r["failures"]
    assert r["plugin"] == {"name": "Demo~Projector", "version": "1.0", "controls": 5, "layout_names_without_control": []}


def test_plugin_layout_naming_a_missing_control_is_reported(tmp_path):
    broken = tmp_path / "broken.qplug"
    broken.write_text(PLUGIN.read_text(encoding="utf-8").replace('layout["Lamp Hours"]', 'layout["Lamp Hour"]'), encoding="utf-8")
    r = server.lua_test(plugin_file=str(broken), steps=[])
    assert not r["ok"] and r["plugin"]["layout_names_without_control"] == ["Lamp Hour"]


def test_strings_keep_their_control_characters():
    assert luatest._lua('a\r\nb"\\') == r'"a\013\010b\"\\"'
