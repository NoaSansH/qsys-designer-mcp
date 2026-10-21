"""Run a Text Controller script outside Designer, against simulated Q-SYS objects.

The script runs in a real Lua interpreter (Lua 5.3 or 5.4: set QSYS_LUA to its path, or have `lua`
on PATH) with mock Controls, Component.New, Timer, NamedControl and print. Steps then drive it:

  {"set": {"control": "Mode", "value": 1}}                  a control of the script (fires its EventHandler)
  {"set": {"component": "MIX", "control": "mute", "value": true}}   another block's control (fires handlers the script set)
  {"advance": 2.5}                                          move the clock on: due timers run
  {"expect": {"control": "Led", "value": true}}             or "string", or "component" for another block

Like Q-SYS, a script writing a control doesn't fire that control's EventHandler; a change from
outside (a step) does.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

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

-- a control: Value, Boolean, String and Position kept consistent
local function newControl(name, min, max)
  local raw = { Value = 0, String = "0", Position = 0 }
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
      for i = 1, d.count do t[d.name][i] = newControl(d.name, d.min, d.max) end
    else
      t[d.name] = newControl(d.name, d.min, d.max)
    end
  end
  return t
end

Controls = controlTable(__DEFS)
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
  end
  __now = stop
end

local function target(s)
  if s.component then return Component.New(s.component)[s.control] end
  local c = Controls[s.control]
  if s.index then c = c[s.index] end
  return c
end

-- the script under test
local ok, err = pcall(function() __SCRIPT() end)
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
    elseif s.expect then
      local c = target(s.expect)
      if c == nil then error("no control " .. tostring(s.expect.control)) end
      local pass, got, want
      if s.expect.string ~= nil then
        got, want = c.String, s.expect.string
        pass = got == want
      elseif type(s.expect.value) == "boolean" then
        got, want = c.Boolean, s.expect.value
        pass = got == want
      else
        got, want = c.Value, s.expect.value
        pass = math.abs(got - want) <= (s.expect.tolerance or 1e-6)
      end
      __results[#__results + 1] = { step = i, kind = "expect", pass = pass, got = __str(got), want = __str(want), label = s.label }
    end
  end)
  if not ok2 then __results[#__results + 1] = { step = i, kind = "error", pass = false, got = tostring(err2), label = s.label } end
end

for _, r in ipairs(__results) do
  io.write("RESULT\t", r.step, "\t", r.kind, "\t", r.pass and "1" or "0", "\t", r.got or "", "\t", r.want or "", "\t", r.label or "", "\n")
end
for _, l in ipairs(__log) do io.write("LOG\t", (l:gsub("\n", " ")), "\n") end
'''


def find_lua() -> str:
    for cand in (os.environ.get("QSYS_LUA"), shutil.which("lua"), shutil.which("lua54"), shutil.which("lua53"),
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Lua" / "bin" / "lua.exe"),
                 r"C:\Program Files\Lua\bin\lua.exe"):
        if cand and Path(cand).is_file():
            return cand
    raise FileNotFoundError("No Lua interpreter found. Install Lua 5.3/5.4 (e.g. 'winget install DEVCOM.Lua') or set QSYS_LUA.")


def _lua(v) -> str:
    """Python value -> Lua literal."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(float(v)) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return "[==[" + v + "]==]" if "]==]" not in v else json.dumps(v)
    if isinstance(v, list):
        return "{" + ", ".join(_lua(x) for x in v) + "}"
    if isinstance(v, dict):
        return "{" + ", ".join(f"[ {_lua(k)} ] = {_lua(x)}" for k, x in v.items()) + "}"   # "[[" would open a long string
    raise TypeError(f"cannot pass {type(v).__name__} to Lua")


def run(lua_code: str, controls: list[dict], steps: list[dict], timeout: float = 20) -> dict:
    """controls: the Text Controller's controls as in the spec (name, type, min, max, count)."""
    defs = [{"name": c["name"], "count": c.get("count", 1),
             "min": c.get("min", 0), "max": c.get("max", 1 if c.get("type") != "knob" else 100)} for c in controls]
    for i, s in enumerate(steps):
        if len([k for k in ("set", "advance", "expect") if k in s]) != 1:
            raise ValueError(f"steps[{i}]: give exactly one of set, advance, expect")
    program = (f"local __DEFS = {_lua(defs)}\nlocal __STEPS = {_lua(steps)}\n"
               f"local function __SCRIPT()\n{lua_code}\nend\n" + HARNESS)
    with tempfile.TemporaryDirectory(prefix="qsys-lua-") as tmp:
        f = Path(tmp) / "run.lua"
        f.write_text(program, encoding="utf-8")
        r = subprocess.run([find_lua(), str(f)], capture_output=True, text=True, timeout=timeout, encoding="utf-8",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    results, log = [], []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if parts[0] == "RESULT":
            step, kind, ok, got, want, label = (parts + [""] * 7)[1:7]
            results.append({"step": int(step), "kind": kind, "pass": ok == "1", "got": got, "want": want, "label": label})
        elif parts[0] == "LOG":
            log.append("\t".join(parts[1:]))
    if r.returncode != 0 and not results:
        return {"ok": False, "error": (r.stderr or r.stdout).strip()[-2000:]}
    checks = [x for x in results if x["kind"] == "expect"]
    errors = [x for x in results if x["kind"] in ("error", "load")]
    return {"ok": not errors and all(x["pass"] for x in checks), "checks": len(checks),
            "passed": sum(x["pass"] for x in checks), "failures": [x for x in results if not x["pass"]], "log": log[-100:]}
