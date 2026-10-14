-- ROOM_MODE: boardroom control logic, v2 (Text Controller).
-- States: Idle, Meeting, Presentation. "Room Off" is an action that makes the room safe
-- (outputs muted, mic TX muted, laptop off) and returns to Idle.
-- The DSP is the source of truth: panel feedback is read back from the DSP controls, and
-- changes made elsewhere (QRC, another panel, the meeting app) are mirrored or, in Idle, refused.
--
-- Teams/Zoom mute sync uses the Core's USB HID Conferencing device (standard HID telephony):
--   spk_led_off_hook / spk_led_mute / spk_led_connected are set BY the PC (call active, app muted, connected)
--   spk_phone_mute is a button we press TO the PC; each press toggles the app's mute.

local MIC_MUTE = Component.New("MIC_MUTE")           -- conferencing TX mute (gain block)
local MASTER   = Component.New("ROOM_MASTER_VOL")    -- room master volume / mute
local LAPTOP   = Component.New("LAPTOP_AUDIO_IN")    -- laptop trim; its mute is the laptop on/off
local CONF_RX  = Component.New("CONF_RX_LEVEL")      -- far-end audio to the room
local HID      = Component.New("USB_HID_CONF")       -- Teams/Zoom call state and mute

local DEFAULT_VOLUME = -20     -- dB, conservative start level for every session
local MIN_VOLUME, MAX_VOLUME = -40, 0
local VOLUME_STEP = 2

local state = "Idle"
local hostTogglePending = false

local function clamp(v) return math.max(MIN_VOLUME, math.min(MAX_VOLUME, v)) end
local function message(text) Controls.StatusMessage.String = text end

local function inCall() return HID["spk_led_off_hook"].Boolean end

-- placeholder: no display is defined in this design. Replace with the display's driver
-- (IP, RS-232 or CEC) once the model is known.
local function setDisplayPower(on)
  print("DISPLAY (placeholder, no display defined): " .. (on and "ON" or "OFF"))
end

-- feedback is always derived from the DSP and HID controls, so the panel cannot disagree with them
local function refresh()
  local micMuted = MIC_MUTE["mute"].Boolean
  local laptopOn = not LAPTOP["mute"].Boolean
  Controls.MicMute.Boolean = micMuted
  Controls.MicMute.Legend = micMuted and "MICS MUTED" or "MICS LIVE"
  Controls.MicMutedLed.Boolean = micMuted
  Controls.ProgramEnable.Boolean = laptopOn
  Controls.ProgramLed.Boolean = laptopOn
  Controls.MasterMute.Boolean = MASTER["mute"].Boolean
  Controls.MasterVolume.Value = clamp(MASTER["gain"].Value)
  Controls.MeetingMode.Boolean = (state == "Meeting")
  Controls.PresentationMode.Boolean = (state == "Presentation")
  Controls.IdleLed.Boolean = (state == "Idle")
  Controls.MeetingLed.Boolean = (state == "Meeting")
  Controls.PresentationLed.Boolean = (state == "Presentation")
  Controls.RoomMode.String = string.upper(state)

  local connected = HID["spk_led_connected"].Boolean
  Controls.PcConnectedLed.Boolean = connected
  Controls.InCallLed.Boolean = inCall()
  if not connected then
    Controls.CallStatus.String = "PC not connected"
  elseif not inCall() then
    Controls.CallStatus.String = "PC connected, no call"
  else
    Controls.CallStatus.String = HID["spk_led_mute"].Boolean and "In call, muted" or "In call, mics live"
  end
end

-- ask the meeting app to match the room mute (one press of the HID mute button, then wait
-- for the app to report back before pressing again)
local function syncHostMute()
  if not inCall() or hostTogglePending then return end
  if HID["spk_led_mute"].Boolean == MIC_MUTE["mute"].Boolean then return end
  hostTogglePending = true
  print("HID: pressing phone mute so the app matches the room (" .. (MIC_MUTE["mute"].Boolean and "mute" or "unmute") .. ")")
  HID["spk_phone_mute"].Boolean = true
  Timer.CallAfter(function() HID["spk_phone_mute"].Boolean = false end, 0.2)
  Timer.CallAfter(function() hostTogglePending = false; syncHostMute() end, 1.5)
end

local function setMicMute(muted)
  MIC_MUTE["mute"].Boolean = muted
  syncHostMute()
end

local function setMode(mode)
  local previous = state
  if mode == "Idle" then
    setMicMute(true)
    LAPTOP["mute"].Boolean = true
    CONF_RX["mute"].Boolean = true
    MASTER["mute"].Boolean = true
    MASTER["gain"].Value = DEFAULT_VOLUME
    setDisplayPower(false)
    message("Room is idle. Choose Meeting or Presentation to start.")
  elseif mode == "Meeting" then
    CONF_RX["mute"].Boolean = false
    MASTER["mute"].Boolean = false
    LAPTOP["mute"].Boolean = true             -- far end is the focus; laptop can be re-enabled
    if previous == "Idle" then setMicMute(false) end
    setDisplayPower(true)
    message("Meeting: microphones live to the call, call audio in the room.")
  elseif mode == "Presentation" then
    CONF_RX["mute"].Boolean = false           -- a call may continue alongside the presentation
    MASTER["mute"].Boolean = false
    LAPTOP["mute"].Boolean = false
    -- mic TX stays as it was; entering from Idle keeps it muted (privacy)
    setDisplayPower(true)
    message("Presentation: laptop audio in the room.")
  else
    error("unknown mode " .. tostring(mode))
  end
  state = mode
  print("ROOM_MODE " .. previous .. " -> " .. mode)
  refresh()
end

local function roomOff()
  -- mute outputs first, then the mic send, then return to Idle
  MASTER["mute"].Boolean = true
  setMicMute(true)
  setMode("Idle")
end

local function setVolume(db)
  if state == "Idle" then
    message("Start Meeting or Presentation to change the volume.")
  else
    MASTER["gain"].Value = clamp(db)
  end
  refresh()
end

-- user controls ------------------------------------------------------------------
-- pressing the mode that is already active changes nothing (keeps the user's laptop/mute choices
-- and doesn't resend display commands); it just restores the button feedback
local function selectMode(mode)
  if state == mode then refresh() else setMode(mode) end
end
Controls.MeetingMode.EventHandler = function() selectMode("Meeting") end
Controls.PresentationMode.EventHandler = function() selectMode("Presentation") end
Controls.RoomOff.EventHandler = function() roomOff() end

Controls.MicMute.EventHandler = function(c)
  if state == "Idle" then
    message("Microphones stay muted while the room is idle.")
  else
    setMicMute(c.Boolean)
  end
  refresh()
end

Controls.ProgramEnable.EventHandler = function(c)
  if state == "Idle" then
    message("Start Meeting or Presentation to play laptop audio.")
  else
    LAPTOP["mute"].Boolean = not c.Boolean
  end
  refresh()
end

Controls.MasterVolume.EventHandler = function(c) setVolume(c.Value) end
Controls.VolumeUp.EventHandler = function() setVolume(MASTER["gain"].Value + VOLUME_STEP) end
Controls.VolumeDown.EventHandler = function() setVolume(MASTER["gain"].Value - VOLUME_STEP) end

Controls.MasterMute.EventHandler = function(c)
  if state ~= "Idle" then MASTER["mute"].Boolean = c.Boolean end
  refresh()
end

-- DSP changes made elsewhere: refuse unmuting while idle, otherwise mirror ------------
local function guardIdle(component, control, after)
  component[control].EventHandler = function(c)
    if state == "Idle" and not c.Boolean then c.Boolean = true end
    if after then after() end
    refresh()
  end
end
guardIdle(MIC_MUTE, "mute", syncHostMute)
guardIdle(MASTER, "mute")
guardIdle(LAPTOP, "mute")
guardIdle(CONF_RX, "mute")
MASTER["gain"].EventHandler = function() refresh() end

-- meeting app -> room -----------------------------------------------------------------
HID["spk_led_mute"].EventHandler = function(c)
  if inCall() then
    if state == "Idle" then
      syncHostMute()                          -- room stays muted while idle; ask the app to mute too
    elseif not hostTogglePending then
      MIC_MUTE["mute"].Boolean = c.Boolean    -- someone muted/unmuted in Teams/Zoom
      print("HID: app " .. (c.Boolean and "muted" or "unmuted") .. ", room follows")
    end
  end
  refresh()
end

HID["spk_led_off_hook"].EventHandler = function()
  print("HID: call " .. (inCall() and "started" or "ended"))
  syncHostMute()                              -- a new call starts with the app matching the room
  refresh()
end

HID["spk_led_connected"].EventHandler = function() refresh() end

setMode("Idle")
