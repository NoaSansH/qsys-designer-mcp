# Q-SYS Lua cheat sheet (Lua 5.3, event-driven)

Scripts never block. Everything happens in EventHandlers and Timers.

## Controls (Control Script / Text Controller / Block Controller / plugin runtime)
```lua
Controls.Gain.Value            -- number
Controls.Gain.String           -- display string ("−6.0dB")
Controls.Gain.Position         -- 0..1
Controls.Mute.Boolean          -- true/false
Controls.Gain.EventHandler = function(ctl) print(ctl.Value) end
Controls.Gain.RampTime = 2 ; Controls.Gain.Value = -10   -- ramp
Controls.Label.Color = "#FF0000" ; Controls.Btn.IsDisabled = true ; Controls.Btn.Legend = "On"
-- Arrays of controls (Count > 1):  Controls.Input[1]
```

## Other components (needs the target's "Script Access" property set)
```lua
local mixer = Component.New("Main Mixer")
mixer["input.1.gain"].Value = -6
mixer["input.1.mute"].EventHandler = function(c) end
for _, c in ipairs(Component.GetControls("Main Mixer")) do print(c.Name) end
Component.GetComponents()          -- list of {Name, Type, Properties}
```

## Named controls
```lua
NamedControl.GetValue("Vol") ; NamedControl.SetValue("Vol", -10, rampSeconds)
NamedControl.GetString / SetString / GetPosition / SetPosition
```

## Timers
```lua
local t = Timer.New()
t.EventHandler = function() --[[ poll ]] end
t:Start(1.0)    -- seconds, repeats
t:Stop()
Timer.CallAfter(function() print("later") end, 2)
```

## TCP
```lua
local s = TcpSocket.New()
s.ReadTimeout, s.WriteTimeout, s.ReconnectTimeout = 0, 0, 5
s.EventHandler = function(sock, evt, err)
  if evt == TcpSocket.Events.Connected then
  elseif evt == TcpSocket.Events.Data then
    local line = sock:ReadLine(TcpSocket.EOL.CrLf)   -- Any, CrLf, Cr, Lf, Null, Custom
  elseif evt == TcpSocket.Events.Closed or evt == TcpSocket.Events.Error or evt == TcpSocket.Events.Timeout then
  end
end
s:Connect("192.168.1.50", 23) ; s:Write("PWR ON\r") ; s:Disconnect()
```
UDP: `UdpSocket.New()`, `:Open(ip, port)`, `:Send(ip, port, data)`, `.Data = function(sock, packet) packet.Data, packet.Address end`.

## HTTP
```lua
HttpClient.Download{ Url = "http://host/api", Headers = {["Accept"]="application/json"}, Timeout = 10,
  EventHandler = function(tbl, code, data, err, headers) end }
HttpClient.Upload{ Url = url, Method = "POST", Data = body, Headers = {["Content-Type"]="application/json"},
  EventHandler = function(tbl, code, data, err, headers) end }
HttpClient.CreateUrl{ Host = "http://host", Path = "api/v1", Query = { a = 1 } }
local rapidjson = require("rapidjson") ; rapidjson.encode(t) ; rapidjson.decode(s)
```

## Serial (Core/peripheral serial ports wired to a script pin)
```lua
local sp = SerialPorts[1]
sp:Open(9600, 8, "N")    -- baud, data bits, parity
sp.EventHandler = function(port, evt) if evt == SerialPorts.Events.Data then port:ReadLine(SerialPorts.EOL.Any) end end
```

## Misc
- `System.IsEmulating`, `System.LockingId`, `System.MajorVersion`, `Design.GetStatus()`
- `Notifications.Publish("topic", tbl)` / `Notifications.Subscribe("topic", function(name, data) end)`
- `print()` goes to the script's debug output; `Log.Message(msg)` / `Log.Error(msg)` go to the Core event log.
- Status controls: 0 OK, 1 Compromised, 2 Fault, 3 Not Present, 4 Missing, 5 Initializing.

## Plugin (.qplug) entry points
`PluginInfo`, `GetColor`, `GetPrettyName`, `GetProperties`, `RectifyProperties`, `GetControls`,
`GetControlLayout` (returns layout, graphics), `GetPins`, `GetComponents`, `GetWiring`.
The runtime code lives inside `if Controls then ... end`. Installed plugins go in
`Documents\QSC\Q-Sys Designer\Plugins` and need a Designer restart (or Tools > Reload Plugins in newer versions).
