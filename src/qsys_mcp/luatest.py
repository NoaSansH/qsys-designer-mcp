"""Run a Text Controller script or a plugin's code outside Designer, against simulated Q-SYS objects.

The script runs in a real Lua interpreter (Lua 5.3 or 5.4: set QSYS_LUA to its path, or have `lua`
on PATH) with mock Controls, Component.New, Timer, NamedControl, TcpSocket, UdpSocket, HttpClient,
json / rapidjson (require), System, Design, Notifications, Uci, Crypto (Base64) and print. Steps then
drive it:

  {"set": {"control": "Mode", "value": 1}}                  a control of the script (fires its EventHandler)
  {"set": {"component": "MIX", "control": "mute", "value": true}}   another block's control (fires handlers the script set)
  {"advance": 2.5}                                          move the clock on: due timers run
  {"socket": {"to": "10.0.0.5:23", "data": "PWR=ON\\r"}}     the device sends data (Data event); or "event":
                                                            "Connected" | "Closed" | "Error" | "Timeout" | "Reconnect"
  {"udp": {"data": "...", "address": "10.0.0.9", "port": 5000}}   a UDP packet arrives
  {"notify": {"name": "RoomCombine", "data": {...}}}        Notifications.Publish from elsewhere
  {"expect": {"control": "Led", "value": true}}             or "string", or "component" for another block
  {"expect": {"sent": "PWR ON\\r", "to": "10.0.0.5:23"}}     the script wrote this since the last "sent" check
  {"expect": {"connected": true, "to": "10.0.0.5:23"}}
  {"expect": {"request": "GET http://10.0.0.7/status"}}     an HTTP request was made (method and URL contain this)
  {"expect": {"uci": "Panel", "page": "Home"}}              Uci.SetPage was called so

Sockets connect at once unless auto_connect is false; HTTP requests get the first response in `http` whose
url is part of the request's (and method matches), delivered before the next step. Like Q-SYS, a script
writing a control doesn't fire that control's EventHandler; a change from outside (a step) does.

A plugin (.qplug) runs as Designer runs it: its file is evaluated without Controls (PluginInfo,
GetProperties, RectifyProperties, GetControls, GetControlLayout, GetPages, GetPins are called with the
properties), then again with the Controls GetControls declared and Properties, for the runtime code.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

LIBS = r'''
-- json / rapidjson ------------------------------------------------------------------------
local __json = { null = setmetatable({}, { __tostring = function() return "null" end }) }
do
  local esc = { ['"'] = '\\"', ['\\'] = '\\\\', ['\b'] = '\\b', ['\f'] = '\\f', ['\n'] = '\\n', ['\r'] = '\\r', ['\t'] = '\\t' }
  local function isarray(t)
    local n = 0
    for k in pairs(t) do
      if type(k) ~= "number" or k <= 0 or k % 1 ~= 0 then return false end
      if k > n then n = k end
    end
    return n == #t
  end
  local function enc(v)
    local t = type(v)
    if v == nil or v == __json.null then return "null"
    elseif t == "boolean" then return v and "true" or "false"
    elseif t == "number" then
      if v ~= v or v == math.huge or v == -math.huge then return "null" end
      if math.type(v) == "integer" or (v % 1 == 0 and math.abs(v) < 1e15) then return string.format("%d", v) end
      return string.format("%.14g", v)
    elseif t == "string" then
      return '"' .. (v:gsub('[%c"\\]', function(c) return esc[c] or string.format("\\u%04x", c:byte()) end)) .. '"'
    elseif t == "table" then
      local out = {}
      if next(v) ~= nil and isarray(v) then
        for i = 1, #v do out[i] = enc(v[i]) end
        return "[" .. table.concat(out, ",") .. "]"
      end
      local keys = {}
      for k in pairs(v) do keys[#keys + 1] = k end
      table.sort(keys, function(a, b) return tostring(a) < tostring(b) end)
      for _, k in ipairs(keys) do out[#out + 1] = enc(tostring(k)) .. ":" .. enc(v[k]) end
      return "{" .. table.concat(out, ",") .. "}"
    end
    error("cannot encode a " .. t)
  end
  local function dec(s, nullv)
    local i = 1
    local function ws() i = s:find("[^ \t\r\n]", i) or #s + 1 end
    local val
    local function str()
      i = i + 1
      local out = {}
      while true do
        local c = s:sub(i, i)
        if c == "" then error("unterminated string") end
        if c == '"' then i = i + 1; break end
        if c == "\\" then
          local e = s:sub(i + 1, i + 1)
          if e == "u" then
            local cp = tonumber(s:sub(i + 2, i + 5), 16)
            i = i + 6
            if cp >= 0xD800 and cp <= 0xDBFF and s:sub(i, i + 1) == "\\u" then
              cp = 0x10000 + (cp - 0xD800) * 0x400 + (tonumber(s:sub(i + 2, i + 5), 16) - 0xDC00)
              i = i + 6
            end
            out[#out + 1] = utf8.char(cp)
          else
            out[#out + 1] = ({ b = "\b", f = "\f", n = "\n", r = "\r", t = "\t" })[e] or e
            i = i + 2
          end
        else
          out[#out + 1] = c
          i = i + 1
        end
      end
      return table.concat(out)
    end
    function val()
      ws()
      local c = s:sub(i, i)
      if c == "{" then
        i = i + 1
        local t = {}
        ws()
        if s:sub(i, i) == "}" then i = i + 1; return t end
        while true do
          ws()
          if s:sub(i, i) ~= '"' then error("expected a key at " .. i) end
          local k = str()
          ws()
          if s:sub(i, i) ~= ":" then error("expected ':' at " .. i) end
          i = i + 1
          t[k] = val()
          ws()
          local d = s:sub(i, i)
          i = i + 1
          if d == "}" then return t elseif d ~= "," then error("expected ',' or '}' at " .. (i - 1)) end
        end
      elseif c == "[" then
        i = i + 1
        local t, n = {}, 0
        ws()
        if s:sub(i, i) == "]" then i = i + 1; return t end
        while true do
          n = n + 1
          t[n] = val()
          ws()
          local d = s:sub(i, i)
          i = i + 1
          if d == "]" then return t elseif d ~= "," then error("expected ',' or ']' at " .. (i - 1)) end
        end
      elseif c == '"' then return str()
      elseif s:find("^true", i) then i = i + 4; return true
      elseif s:find("^false", i) then i = i + 5; return false
      elseif s:find("^null", i) then i = i + 4; return nullv
      end
      local num = s:match("^-?%d+%.?%d*[eE]?[-+]?%d*", i)
      if not num then error("unexpected '" .. c .. "' at " .. i) end
      i = i + #num
      return math.tointeger(tonumber(num)) or tonumber(num)
    end
    local r = val()
    ws()
    if i <= #s then error("trailing data at " .. i) end
    return r
  end
  __json.encode = function(v) return enc(v) end
  __json.decode = function(s, nullv)
    local ok, r = pcall(dec, s, nullv)
    if ok then return r end
    return nil, r
  end
end
package.preload["rapidjson"] = function()
  return { encode = __json.encode, null = __json.null, object = function(t) return t or {} end, array = function(t) return t or {} end,
           decode = function(s) return __json.decode(s, __json.null) end }
end
package.preload["json"] = function() return { encode = __json.encode, decode = function(s) return __json.decode(s, nil) end, null = __json.null } end
'''

HARNESS = r'''
local __results, __log = {}, {}
local function __str(v)
  if type(v) == "boolean" then return v and "true" or "false" end
  if type(v) == "number" then return string.format("%.6g", v) end
  return tostring(v)
end
print = function(...)
  local parts = {}
  for i = 1, select("#", ...) do parts[#parts + 1] = tostring(select(i, ...)) end
  __log[#__log + 1] = table.concat(parts, "\t")
end

-- callbacks Q-SYS runs after the current one (socket events, HTTP replies, notifications)
local __pending = {}
local function later(f) __pending[#__pending + 1] = f end
local function flush()
  local n = 0
  while #__pending > 0 and n < 10000 do
    table.remove(__pending, 1)()
    n = n + 1
  end
end

-- a control: Value, Boolean, String and Position kept consistent
local function newControl(name, min, max, text)
  local raw = { Value = 0, String = text and "" or "0", Position = 0 }
  local c = { Name = name, EventHandler = nil, Choices = {}, Legend = "", Color = "", IsDisabled = false, IsInvisible = false }
  min, max = min or 0, max or 1
  local function setValue(v)
    raw.Value = v
    raw.String = __str(v)
    raw.Position = (max > min) and math.max(0, math.min(1, (v - min) / (max - min))) or 0
  end
  setmetatable(c, {
    __index = function(_, k)
      if k == "Value" or k == "String" or k == "Position" then return raw[k] end
      if k == "Boolean" then return raw.Value ~= 0 end
      return nil
    end,
    __newindex = function(t, k, v)
      if k == "Value" then setValue(tonumber(v) or 0)
      elseif k == "Boolean" then setValue(v and 1 or 0)
      elseif k == "Position" then setValue(min + (max - min) * v)
      elseif k == "String" then
        local n = tonumber(v)
        if n then setValue(n) else raw.String = tostring(v) end
      else rawset(t, k, v) end
    end })
  return c
end

local function controlTable(defs)
  local t = {}
  for _, d in ipairs(defs) do
    if (d.count or 1) > 1 then
      t[d.name] = {}
      for i = 1, d.count do t[d.name][i] = newControl(d.name, d.min, d.max, d.text) end
    else
      t[d.name] = newControl(d.name, d.min, d.max, d.text)
    end
  end
  return t
end

local __components = {}
Component = { New = function(name)
  if not __components[name] then
    local controls = {}
    __components[name] = setmetatable({}, {
      __index = function(_, k)
        if not controls[k] then controls[k] = newControl(k, 0, 1) end
        return controls[k]
      end })
  end
  return __components[name]
end }
local __named = {}
NamedControl = {
  GetValue = function(n) return __named[n] or 0 end, SetValue = function(n, v) __named[n] = v end,
  GetString = function(n) return __str(__named[n] or "") end, SetString = function(n, v) __named[n] = v end,
  GetPosition = function(n) return __named[n] or 0 end, SetPosition = function(n, v) __named[n] = v end }

-- timers on a simulated clock
local __now, __timers = 0, {}
local function schedule(t) table.insert(__timers, t) end
Timer = {
  CallAfter = function(f, delay) schedule({ at = __now + delay, f = f }) end,
  New = function()
    local tm = { EventHandler = nil, _period = nil }
    function tm:Start(period) self._period = period; schedule({ at = __now + period, timer = self }) end
    function tm:Stop() self._period = nil end
    function tm:IsRunning() return self._period ~= nil end
    return tm
  end }
local function advance(seconds)
  local stop = __now + seconds
  while true do
    table.sort(__timers, function(a, b) return a.at < b.at end)
    local nxt = __timers[1]
    if not nxt or nxt.at > stop then break end
    table.remove(__timers, 1)
    __now = nxt.at
    if nxt.f then nxt.f()
    elseif nxt.timer and nxt.timer._period then
      local period = nxt.timer._period
      if nxt.timer.EventHandler then nxt.timer.EventHandler(nxt.timer) end
      if nxt.timer._period then schedule({ at = __now + period, timer = nxt.timer }) end
    end
    flush()
  end
  __now = stop
end

-- sockets ---------------------------------------------------------------------------------
local __sockets, __sent, __udp, __lastSent = {}, {}, {}, nil
local function key(h, p) return tostring(h) .. ":" .. tostring(p) end
local function sentTo(k) __sent[k] = __sent[k] or { all = {}, since = {} }; return __sent[k] end
local function record(k, data) local t = sentTo(k); t.all[#t.all + 1] = tostring(data); t.since[#t.since + 1] = tostring(data); __lastSent = k end
TcpSocket = { Events = { Connected = "CONNECTED", Reconnect = "RECONNECT", Data = "DATA", Closed = "CLOSED", Error = "ERROR", Timeout = "TIMEOUT" },
              EOL = { Any = "Any", CrLf = "CrLf", CrLfStrict = "CrLfStrict", Lf = "Lf", Null = "Null", Custom = "Custom" } }
local EVENT_FIELD = { CONNECTED = "Connected", RECONNECT = "Reconnect", DATA = "Data", CLOSED = "Closed", ERROR = "Error", TIMEOUT = "Timeout" }
local function fire(sock, evt, err)
  if evt == "CONNECTED" then sock.IsConnected = true elseif evt == "CLOSED" or evt == "ERROR" then sock.IsConnected = false end
  if sock.EventHandler then sock.EventHandler(sock, evt, err) end
  local f = rawget(sock, EVENT_FIELD[evt])
  if type(f) == "function" then f(sock, err) end
end
function TcpSocket.New()
  local s = { IsConnected = false, ReadTimeout = 0, WriteTimeout = 0, ReconnectTimeout = 5, _buf = "" }
  function s:Connect(host, port)
    self._key = key(host, port)
    __sockets[self._key] = self
    sentTo(self._key)
    if __AUTOCONNECT then later(function() fire(self, "CONNECTED") end) end
  end
  function s:Disconnect() self.IsConnected = false end
  function s:Write(data) if self._key then record(self._key, data) end end
  function s:Read(n)
    n = n or #self._buf
    local r = self._buf:sub(1, n)
    self._buf = self._buf:sub(n + 1)
    return r
  end
  function s:ReadLine(eol, custom)
    local buf, a, b = self._buf, nil, nil
    if eol == "Custom" then a, b = buf:find(custom, 1, true)
    elseif eol == "CrLf" or eol == "CrLfStrict" then a, b = buf:find("\r\n", 1, true)
    elseif eol == "Lf" then a, b = buf:find("\n", 1, true)
    elseif eol == "Null" then a, b = buf:find("\0", 1, true)
    else
      a = buf:find("[\r\n]")
      if a then
        b = a
        local nxt = buf:sub(a + 1, a + 1)
        if (nxt == "\r" or nxt == "\n") and nxt ~= buf:sub(a, a) then b = a + 1 end
      end
    end
    if not a then return nil end
    self._buf = buf:sub(b + 1)
    return buf:sub(1, a - 1)
  end
  function s:Search(str, start) return self._buf:find(str, start or 1, true) end
  return setmetatable(s, { __index = function(t, k) if k == "BufferLength" then return #rawget(t, "_buf") end end })
end
UdpSocket = { New = function()
  local u = { EventHandler = nil }
  function u:Open(ip, port) self._port = port; __udp[#__udp + 1] = self end
  function u:Close() end
  function u:JoinMulticast() end
  function u:Send(ip, port, data) record(key(ip, port), data) end
  return u
end }

-- HTTP ------------------------------------------------------------------------------------
local __requests = {}
local function httpCall(t, method)
  local req = { method = (t.Method or method):upper(), url = t.Url or "", data = t.Data or "" }
  __requests[#__requests + 1] = req
  later(function()
    local r
    for _, cand in ipairs(__HTTP) do
      if (not cand.method or cand.method:upper() == req.method) and req.url:find(cand.url or "", 1, true) then r = cand; break end
    end
    local code, data, err, headers = 404, "", "no simulated response for " .. req.method .. " " .. req.url, {}
    if r then
      code, err, headers = r.code or 200, r.error, r.headers or {}
      data = type(r.data) == "table" and __json.encode(r.data) or (r.data or "")
    end
    if t.EventHandler then t.EventHandler(t, code, data, err, headers) end
  end)
end
local function urlenc(s) return (tostring(s):gsub("[^%w%-%._~]", function(c) return string.format("%%%02X", c:byte()) end)) end
HttpClient = {
  Download = function(t) httpCall(t, "GET") end,
  Upload = function(t) httpCall(t, "POST") end,
  EncodeString = urlenc,
  DecodeString = function(s) return (s:gsub("%%(%x%x)", function(h) return string.char(tonumber(h, 16)) end)) end,
  EncodeParams = function(p)
    local keys, out = {}, {}
    for k in pairs(p) do keys[#keys + 1] = k end
    table.sort(keys)
    for _, k in ipairs(keys) do out[#out + 1] = urlenc(k) .. "=" .. urlenc(p[k]) end
    return table.concat(out, "&")
  end }
HttpClient.CreateUrl = function(t)
  local url = (t.Host or "") .. (t.Port and (":" .. t.Port) or "")
  if t.Path then url = url .. "/" .. tostring(t.Path):gsub("^/", "") end
  if t.Query and next(t.Query) then url = url .. "?" .. HttpClient.EncodeParams(t.Query) end
  return url
end

-- System, Design, Notifications, Uci, Crypto -----------------------------------------------
System = { IsEmulating = true, LockingMode = "Unlocked", MajorVersion = 9, MinorVersion = 13, Version = "9.13.2",
           IsRedundant = false, IsBackupActive = false, CoreModel = "Core 8 Flex", Core = "Core-1" }
for k, v in pairs(__SYSTEM) do System[k] = v end
Design = { GetStatus = function() return { Status = "OK", Code = 0, DesignName = "test", DesignCode = "TEST", IsRedundant = false,
                                           IsEmulator = System.IsEmulating } end,
           GetInventory = function() return {} end }
local __subs = {}
Notifications = {
  Subscribe = function(name, f) __subs[name] = __subs[name] or {}; table.insert(__subs[name], f); return #__subs[name] end,
  Unsubscribe = function() end,
  Publish = function(name, data) for _, f in ipairs(__subs[name] or {}) do later(function() f(name, data) end) end end }
local __uci = {}
Uci = { SetPage = function(uci, page) __uci[uci] = page end, SetLayerVisibility = function() end,
        SetSharedLayerVisibility = function() end }
do
  local b = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
  Crypto = {
    Base64Encode = function(data)
      return ((data:gsub(".", function(x)
        local r, byte = "", x:byte()
        for i = 8, 1, -1 do r = r .. (byte % 2 ^ i - byte % 2 ^ (i - 1) > 0 and "1" or "0") end
        return r
      end) .. "0000"):gsub("%d%d%d?%d?%d?%d?", function(x)
        if #x < 6 then return "" end
        local c = 0
        for i = 1, 6 do c = c + (x:sub(i, i) == "1" and 2 ^ (6 - i) or 0) end
        return b:sub(c + 1, c + 1)
      end) .. ({ "", "==", "=" })[#data % 3 + 1])
    end,
    Base64Decode = function(data)
      data = data:gsub("[^" .. b .. "=]", "")
      return (data:gsub(".", function(x)
        if x == "=" then return "" end
        local r, f = "", (b:find(x, 1, true) - 1)
        for i = 6, 1, -1 do r = r .. (f % 2 ^ i - f % 2 ^ (i - 1) > 0 and "1" or "0") end
        return r
      end):gsub("%d%d%d?%d?%d?%d?%d?%d?", function(x)
        if #x ~= 8 then return "" end
        local c = 0
        for i = 1, 8 do c = c + (x:sub(i, i) == "1" and 2 ^ (8 - i) or 0) end
        return string.char(c)
      end))
    end,
    Digest = function() error("Crypto.Digest is not simulated") end,
    HMAC = function() error("Crypto.HMAC is not simulated") end }
end

local function target(s)
  if s.component then return Component.New(s.component)[s.control] end
  local c = Controls[s.control]
  if s.index then c = c[s.index] end
  return c
end
local function socketFor(to)
  if to then return __sockets[to] end
  for _, s in pairs(__sockets) do return s end
end
local function shown(t) return (table.concat(t, ""):gsub("[%c]", function(c) return string.format("\\%d", c:byte()) end)) end

-- plugin: design-time pass, then controls from GetControls -----------------------------------
local __plugin = { }
local function controlDefs(list)
  local defs = {}
  for _, c in ipairs(list or {}) do
    local t = c.ControlType or ""
    local min, max = c.Min, c.Max
    if not min then
      if t == "Knob" then
        local unit = c.ControlUnit or ""
        min, max = ({ dB = -100, Hz = 20, Percent = 0, Pan = -1, Seconds = 0 })[unit] or 0, ({ dB = 20, Hz = 20000, Percent = 100, Pan = 1, Seconds = 60 })[unit] or 100
      else min, max = 0, 1 end
    end
    defs[#defs + 1] = { name = c.Name, count = c.Count or 1, min = min, max = max,
                        text = t == "Text" or (t == "Indicator" and c.IndicatorType == "Text") }
  end
  return defs
end
local function pluginDesignTime()
  Controls, Properties = nil, nil
  __SCRIPT()
  local props = {}
  for _, p in ipairs(type(GetProperties) == "function" and GetProperties() or {}) do props[p.Name] = p end
  for k, v in pairs(__PROPS) do props[k] = props[k] or { Name = k }; props[k].Value = v end
  if type(RectifyProperties) == "function" then props = RectifyProperties(props) or props end
  local list = type(GetControls) == "function" and GetControls(props) or {}
  local names, problems = {}, {}
  for _, c in ipairs(list) do names[c.Name] = c.Count or 1 end
  if type(GetControlLayout) == "function" then
    local layout = GetControlLayout(props) or {}
    for k in pairs(layout) do
      local base, n = tostring(k):match("^(.-) (%d+)$")
      if not names[k] and not (base and names[base] and tonumber(n) <= names[base]) then problems[#problems + 1] = tostring(k) end
    end
  end
  for _, f in ipairs({ "GetPages", "GetPins", "GetColor", "GetPrettyName" }) do
    if type(_G[f]) == "function" then _G[f](props) end
  end
  __plugin = { name = PluginInfo and PluginInfo.Name or "?", version = PluginInfo and PluginInfo.Version or "?",
               controls = #list, layout_unknown = problems }
  Properties = props
  return controlDefs(list)
end

local ok, err = pcall(function()
  if __PLUGIN then
    Controls = controlTable(pluginDesignTime())
  else
    Controls = controlTable(__DEFS)
  end
  __SCRIPT()
  flush()
end)
if not ok then __results[#__results + 1] = { step = 0, kind = "load", pass = false, got = tostring(err) } end

for i, s in ipairs(__STEPS) do
  local ok2, err2 = pcall(function()
    if s.set then
      local c = target(s.set)
      if c == nil then error("no control " .. tostring(s.set.control)) end
      if type(s.set.value) == "boolean" then c.Boolean = s.set.value
      elseif type(s.set.value) == "string" then c.String = s.set.value
      else c.Value = s.set.value end
      if c.EventHandler then c.EventHandler(c) end
    elseif s.advance then
      advance(s.advance)
    elseif s.socket then
      local sock = socketFor(s.socket.to)
      if not sock then error("no socket " .. tostring(s.socket.to) .. " was opened") end
      if s.socket.data then
        sock._buf = sock._buf .. s.socket.data
        fire(sock, "DATA")
      end
      if s.socket.event then fire(sock, string.upper(s.socket.event), s.socket.error) end
    elseif s.udp then
      for _, u in ipairs(__udp) do
        if u.EventHandler then u.EventHandler(u, { Data = s.udp.data, Address = s.udp.address or "0.0.0.0", Port = s.udp.port or 0 }) end
      end
    elseif s.notify then
      Notifications.Publish(s.notify.name, s.notify.data)
    elseif s.expect then
      local e = s.expect
      local pass, got, want
      if e.sent ~= nil then
        local to = e.to or __lastSent
        local t = __sent[to or ""]
        got, want = t and shown(t.since) or "(nothing sent)", shown({ e.sent })
        pass = t ~= nil and table.concat(t.since, ""):find(e.sent, 1, true) ~= nil
        if t then t.since = {} end
      elseif e.connected ~= nil then
        local sock = socketFor(e.to)
        got, want = sock and sock.IsConnected or false, e.connected
        pass = got == want
      elseif e.request ~= nil then
        local all = {}
        pass = false
        for _, r in ipairs(__requests) do
          local line = r.method .. " " .. r.url
          all[#all + 1] = line
          if line:find(e.request, 1, true) and (not e.body or r.data:find(e.body, 1, true)) then pass = true end
        end
        got, want = table.concat(all, "; "), e.request
      elseif e.uci ~= nil then
        got, want = __uci[e.uci] or "(not set)", e.page
        pass = got == want
      else
        local c = target(e)
        if c == nil then error("no control " .. tostring(e.control)) end
        if e.string ~= nil then
          got, want = c.String, e.string
          pass = got == want
        elseif type(e.value) == "boolean" then
          got, want = c.Boolean, e.value
          pass = got == want
        else
          got, want = c.Value, e.value
          pass = math.abs(got - want) <= (e.tolerance or 1e-6)
        end
      end
      __results[#__results + 1] = { step = i, kind = "expect", pass = pass, got = __str(got), want = __str(want), label = s.label }
    end
    flush()
  end)
  if not ok2 then __results[#__results + 1] = { step = i, kind = "error", pass = false, got = tostring(err2), label = s.label } end
end

for _, r in ipairs(__results) do
  io.write("RESULT\t", r.step, "\t", r.kind, "\t", r.pass and "1" or "0", "\t", (tostring(r.got or ""):gsub("[\t\n]", " ")), "\t",
           (tostring(r.want or ""):gsub("[\t\n]", " ")), "\t", r.label or "", "\n")
end
for _, l in ipairs(__log) do io.write("LOG\t", (l:gsub("\n", " ")), "\n") end
for k, t in pairs(__sent) do io.write("SENT\t", k, "\t", shown(t.all), "\n") end
for _, r in ipairs(__requests) do io.write("REQUEST\t", r.method, " ", r.url, "\n") end
if __PLUGIN then
  io.write("PLUGIN\t", tostring(__plugin.name), "\t", tostring(__plugin.version), "\t", tostring(__plugin.controls or 0), "\t",
           table.concat(__plugin.layout_unknown or {}, "|"), "\n")
end
'''

STEP_KINDS = ("set", "advance", "expect", "socket", "udp", "notify")


def find_lua() -> str:
    for cand in (os.environ.get("QSYS_LUA"), shutil.which("lua"), shutil.which("lua54"), shutil.which("lua53"),
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Lua" / "bin" / "lua.exe"),
                 r"C:\Program Files\Lua\bin\lua.exe"):
        if cand and Path(cand).is_file():
            return cand
    raise FileNotFoundError("No Lua interpreter found. Install Lua 5.3/5.4 (e.g. 'winget install DEVCOM.Lua') or set QSYS_LUA.")


def _lua_str(s: str) -> str:
    """A quoted Lua string; control characters escaped (a long bracket string would turn \\r\\n into \\n)."""
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append(f"\\{ord(ch):03d}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _lua(v) -> str:
    """Python value -> Lua literal."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v)) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return _lua_str(v)
    if isinstance(v, list):
        return "{" + ", ".join(_lua(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ", ".join(f"[ {_lua(k)} ] = {_lua(x)}" for k, x in v.items()) + "}"   # "[[" would open a long string
    raise TypeError(f"cannot pass {type(v).__name__} to Lua")


def run(lua_code: str, controls: list[dict], steps: list[dict], timeout: float = 20, *, http: list[dict] | None = None,
        system: dict | None = None, plugin: bool = False, properties: dict | None = None, auto_connect: bool = True) -> dict:
    """controls: the Text Controller's controls as in the spec (name, type, min, max, count); for a plugin
    they come from its GetControls. http: [{"url", "method"?, "code"?, "data" (text or JSON object), "error"?,
    "headers"?}]. system: System fields (IsEmulating, LockingMode...). properties: a plugin's property values."""
    defs = [{"name": c["name"], "count": c.get("count", 1), "text": c.get("type") in ("text", "status_text"),
             "min": c.get("min", 0), "max": c.get("max", 1 if c.get("type") != "knob" else 100)} for c in controls]
    for i, s in enumerate(steps):
        if len([k for k in STEP_KINDS if k in s]) != 1:
            raise ValueError(f"steps[{i}]: give exactly one of {', '.join(STEP_KINDS)}")
    program = (f"local __DEFS = {_lua(defs)}\nlocal __STEPS = {_lua(steps)}\nlocal __HTTP = {_lua(http or [])}\n"
               f"local __SYSTEM = {_lua(system or {})}\nlocal __PROPS = {_lua(properties or {})}\n"
               f"local __PLUGIN = {_lua(plugin)}\nlocal __AUTOCONNECT = {_lua(auto_connect)}\n"
               f"local function __SCRIPT()\n{lua_code}\nend\n" + LIBS + HARNESS)
    with tempfile.TemporaryDirectory(prefix="qsys-lua-") as tmp:
        f = Path(tmp) / "run.lua"
        f.write_text(program, encoding="utf-8")
        r = subprocess.run([find_lua(), str(f)], capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    results, log, sent, requests, plugin_info = [], [], {}, [], None
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "RESULT":
            step, kind, ok, got, want, label = (parts + [""] * 7)[1:7]
            results.append({"step": int(step), "kind": kind, "pass": ok == "1", "got": got, "want": want, "label": label})
        elif parts[0] == "LOG":
            log.append("\t".join(parts[1:]))
        elif parts[0] == "SENT":
            sent[parts[1]] = parts[2] if len(parts) > 2 else ""
        elif parts[0] == "REQUEST":
            requests.append(parts[1])
        elif parts[0] == "PLUGIN":
            name, version, n, unknown = (parts + [""] * 5)[1:5]
            plugin_info = {"name": name, "version": version, "controls": int(n or 0),
                           "layout_names_without_control": [x for x in unknown.split("|") if x]}
    if r.returncode != 0 and not results:
        return {"ok": False, "error": (r.stderr or r.stdout).strip()[-2000:]}
    checks = [x for x in results if x["kind"] == "expect"]
    errors = [x for x in results if x["kind"] in ("error", "load")]
    out = {"ok": not errors and all(x["pass"] for x in checks) and not (plugin_info or {}).get("layout_names_without_control"),
           "checks": len(checks), "passed": sum(x["pass"] for x in checks), "failures": [x for x in results if not x["pass"]],
           "log": log[-100:]}
    if sent:
        out["sent"] = sent
    if requests:
        out["requests"] = requests[-50:]
    if plugin_info:
        out["plugin"] = plugin_info
    return out
