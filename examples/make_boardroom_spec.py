"""Writes examples/boardroom.json, the boardroom design used as the worked example and end-to-end test.

The spec is plain JSON; this script only exists so the repeated parts (per-mic chains, per-speaker
outputs, UCI layout) stay short and readable. Run: python examples/make_boardroom_spec.py
"""
import json
from pathlib import Path

C: list[dict] = []  # components


def comp(name, cls=None, x=0, y=0, props=None, values=None, access="None", **kw):
    c = {"name": name, "x": x, "y": y}
    if cls:
        c["class"] = cls
    if props:
        c["properties"] = props
    if values:
        c["values"] = values
    c["script_access"] = access
    c.update(kw)
    C.append(c)


comp("FLEX_IN", inventory="Flex In", x=40, y=60, values={
    **{f"channel_{ch}_preamp_gain": 36 for ch in (1, 2)},
    **{f"channel_{ch}_sensitivity": -12 for ch in (1, 2)},        # linked to preamp gain: store both
    **{f"channel_{ch}_phantom_power": True for ch in (1, 2)},
    **{f"channel_{ch}_flex_input_enable": ch <= 4 for ch in range(1, 9)}})
comp("FLEX_OUT", inventory="Flex Out", x=1940, y=560,
     values={f"channel_{ch}_flex_output_enable": ch >= 5 for ch in range(1, 9)})
comp("USB_RX_CONF", inventory="USB Sound Card 1 In", x=40, y=760)
comp("USB_TX_CONF", inventory="USB Sound Card 1 Out", x=1760, y=120)
comp("USB_HID_CONF", inventory="HID Conferencing", x=260, y=1000, access="Script")
for i in (1, 2):
    y = 40 + (i - 1) * 180
    comp(f"MIC_0{i}_HPF", "filter_highpass", 260, y, {"multi_channel_type": 1}, {"frequency": 100})
    comp(f"MIC_0{i}_EQ", "equalizer_parametric", 460, y, {"multi_channel_type": 1, "n_bands": 4})
    comp(f"AEC_0{i}", "acoustic_echo_canceler_simd", 680, y, {"channel_count": 1})
comp("MIC_INPUT_METER", "meter2", 260, 400, {"multi_channel_type": 3, "multi_channel_count": 2})
comp("CONF_AUTOMIXER", "auto_mixer_gating_adaptive", 900, 100, {"n_channels": 2, "output_choice": 3})
comp("CONF_TX_LEVELER", "leveler2", 1120, 120, {"multi_channel_type": 1})
comp("CONF_TX_COMP", "compressor", 1340, 120, {"multi_channel_type": 1}, {"ratio": 2})
comp("MIC_MUTE", "gain", 1550, 120, {"multi_channel_type": 1}, {"mute": True}, access="Script")
comp("TX_METER", "meter2", 1760, 300, {"multi_channel_type": 1})
comp("LAPTOP_AUDIO_IN", "gain", 460, 560, {"multi_channel_type": 2}, {"gain": -6, "mute": True}, access="Script")
comp("CONF_RX_LEVEL", "gain", 460, 760, {"multi_channel_type": 2}, {"gain": -6}, access="Script")
comp("ROOM_PROGRAM_MIX", "mixer", 680, 620, {"n_inputs": 4, "n_outputs": 1},
     {f"input_{i}_output_1_gain": -6 for i in range(1, 5)})
comp("ROOM_MASTER_VOL", "gain", 900, 640, {"multi_channel_type": 1, "max_gain": 0, "min_gain": -100},
     {"gain": -20, "mute": True}, access="Script")
for i in range(1, 5):
    comp(f"SPK_OUT_0{i}", "gain", 1120, 520 + (i - 1) * 110, {"multi_channel_type": 1, "max_gain": 6})
comp("SPK_HPF", "filter_highpass", 1340, 560, {"multi_channel_type": 3, "multi_channel_count": 4}, {"frequency": 80})
comp("SPK_EQ", "equalizer_parametric", 1550, 560, {"multi_channel_type": 3, "multi_channel_count": 4, "n_bands": 4})
comp("SPK_LIMITER", "limiter_peak", 1760, 560, {"multi_channel_type": 3, "multi_channel_count": 4}, {"threshold_level": -10})
comp("SPK_METER", "meter2", 1940, 860, {"multi_channel_type": 3, "multi_channel_count": 4})

controls = [{"name": n, "type": "button", "button_type": "toggle"}
            for n in ("MeetingMode", "PresentationMode", "MasterMute", "MicMute", "ProgramEnable")]
controls += [{"name": n, "type": "button", "button_type": "trigger"} for n in ("RoomOff", "VolumeUp", "VolumeDown")]
controls += [{"name": "MasterVolume", "type": "knob", "unit": "dB", "min": -40, "max": 0}]
controls += [{"name": n, "type": "status_text"} for n in ("RoomMode", "StatusMessage", "CallStatus")]
controls += [{"name": n, "type": "led"} for n in
             ("IdleLed", "MeetingLed", "PresentationLed", "MicMutedLed", "ProgramLed", "PcConnectedLed", "InCallLed")]
comp("ROOM_MODE", "device_controller_script", 40, 1000, access="External",
     text_controller={"controls": controls, "lua_file": "boardroom_room_mode.lua"})

W: list[list[str]] = []


def w(a, b):
    W.append([a, b])


for i in (1, 2):
    n = f"0{i}"
    w(f"FLEX_IN.out.{i}", f"MIC_{n}_HPF.in.1")
    w(f"FLEX_IN.out.{i}", f"MIC_INPUT_METER.in.{i}")
    w(f"MIC_{n}_HPF.out.1", f"MIC_{n}_EQ.in.1")
    w(f"MIC_{n}_EQ.out.1", f"AEC_{n}.in.Microphone")
    w("ROOM_MASTER_VOL.out.1", f"AEC_{n}.in.Reference")        # echo reference = what the speakers play
    w(f"AEC_{n}.out.1", f"CONF_AUTOMIXER.in.{i}")
w("CONF_AUTOMIXER.out.Mix Output", "CONF_TX_LEVELER.in.1")
w("CONF_TX_LEVELER.out.1", "CONF_TX_COMP.in.1")
w("CONF_TX_COMP.out.1", "MIC_MUTE.in.1")
w("MIC_MUTE.out.1", "USB_TX_CONF.in.1")
w("MIC_MUTE.out.1", "USB_TX_CONF.in.2")
w("MIC_MUTE.out.1", "TX_METER.in.1")
w("FLEX_IN.out.3", "LAPTOP_AUDIO_IN.in.1")
w("FLEX_IN.out.4", "LAPTOP_AUDIO_IN.in.2")
w("LAPTOP_AUDIO_IN.out.1", "ROOM_PROGRAM_MIX.in.3")
w("LAPTOP_AUDIO_IN.out.2", "ROOM_PROGRAM_MIX.in.4")
w("USB_RX_CONF.out.1", "CONF_RX_LEVEL.in.1")
w("USB_RX_CONF.out.2", "CONF_RX_LEVEL.in.2")
w("CONF_RX_LEVEL.out.1", "ROOM_PROGRAM_MIX.in.1")
w("CONF_RX_LEVEL.out.2", "ROOM_PROGRAM_MIX.in.2")
w("ROOM_PROGRAM_MIX.out.1", "ROOM_MASTER_VOL.in.1")
for i in range(1, 5):
    w("ROOM_MASTER_VOL.out.1", f"SPK_OUT_0{i}.in.1")
    w(f"SPK_OUT_0{i}.out.1", f"SPK_HPF.in.{i}")
    w(f"SPK_HPF.out.{i}", f"SPK_EQ.in.{i}")
    w(f"SPK_EQ.out.{i}", f"SPK_LIMITER.in.{i}")
    w(f"SPK_LIMITER.out.{i}", f"FLEX_OUT.in.{i + 4}")
    w(f"SPK_LIMITER.out.{i}", f"SPK_METER.in.{i}")

WHITE, GREY, OFF = "#FFFFFFFF", "#FF9AA0A6", "#FF3A3B40"
E: list[dict] = []


def text(s, x, y, wd, h, color=WHITE, size=13, align="Left"):
    E.append({"type": "text", "text": s, "x": x, "y": y, "w": wd, "h": h, "color": color, "font_size": size, "align": align})


def ctl(component, control, x, y, wd, h, **kw):
    E.append({"type": "control", "component": component, "control": control, "x": x, "y": y, "w": wd, "h": h, **kw})


big = {"text_color": WHITE, "font_size": 24, "corner_radius": 10}
text("BOARDROOM", 30, 20, 400, 50, size=30)
text("Mode", 660, 24, 90, 44, GREY, 16, "Right")
ctl("ROOM_MODE", "RoomMode", 760, 24, 300, 44, style="Text", color="#FF2B2D33", text_color=WHITE, font_size=20, corner_radius=6)
ctl("ROOM_MODE", "StatusMessage", 30, 80, 1030, 34, style="Text", color="#FF1C1D21", text_color=GREY, font_size=14)
ctl("ROOM_MODE", "MeetingMode", 30, 130, 320, 110, label="MEETING", color="#FF2E7D32", off_color=OFF, **big)
ctl("ROOM_MODE", "PresentationMode", 385, 130, 320, 110, label="PRESENTATION", color="#FF1565C0", off_color=OFF, **big)
ctl("ROOM_MODE", "RoomOff", 740, 130, 320, 110, label="ROOM OFF", color="#FFC62828", off_color="#FF5A2A2A", **big)
ctl("ROOM_MODE", "MeetingLed", 175, 250, 30, 30, color="#FF4CAF50")
ctl("ROOM_MODE", "PresentationLed", 530, 250, 30, 30, color="#FF42A5F5")
ctl("ROOM_MODE", "IdleLed", 885, 250, 30, 30, color="#FFFFB300")
text("Idle / standby", 820, 282, 160, 24, GREY, 13, "Center")
text("ROOM VOLUME", 30, 320, 300, 30, size=18)
ctl("ROOM_MODE", "MasterVolume", 40, 360, 90, 400, style="Fader", color="#FFD6D9DE")
ctl("ROOM_MODE", "MasterMute", 150, 360, 170, 60, label="MUTE ROOM", color="#FFC62828", text_color=WHITE, font_size=18,
    corner_radius=8, off_color=OFF)
ctl("ROOM_MODE", "VolumeUp", 150, 430, 80, 60, label="+", color="#FF5F6368", text_color=WHITE, font_size=26, corner_radius=8, off_color=OFF)
ctl("ROOM_MODE", "VolumeDown", 240, 430, 80, 60, label="-", color="#FF5F6368", text_color=WHITE, font_size=26, corner_radius=8, off_color=OFF)
text("Speaker levels", 150, 500, 200, 24, GREY)
for i in range(1, 5):
    ctl("SPK_METER", f"meter_{i}", 150 + (i - 1) * 42, 530, 34, 200, style="Meter")
    text(f"SPK {i}", 144 + (i - 1) * 42, 734, 46, 22, GREY, 11, "Center")
text("MICROPHONES", 400, 320, 300, 30, size=18)
ctl("ROOM_MODE", "MicMute", 400, 360, 300, 110, color="#FFC62828", off_color="#FF2E7D32", **big)   # legend set by the script
ctl("ROOM_MODE", "MicMutedLed", 400, 490, 30, 30, color="#FFFF5252")
text("Mics muted (far end cannot hear the room)", 440, 490, 300, 30, GREY)
text("Mic send level", 400, 540, 200, 24, GREY)
ctl("TX_METER", "meter_1", 400, 570, 300, 34, style="Meter")
text("LAPTOP AUDIO", 760, 320, 300, 30, size=18)
ctl("ROOM_MODE", "ProgramEnable", 760, 360, 300, 110, label="LAPTOP AUDIO", color="#FF1565C0", off_color=OFF, **big)
ctl("ROOM_MODE", "ProgramLed", 760, 490, 30, 30, color="#FF42A5F5")
text("Laptop audio playing in room", 800, 490, 260, 30, GREY)
text("CONFERENCE PC", 760, 560, 300, 30, size=18)
ctl("ROOM_MODE", "PcConnectedLed", 760, 600, 30, 30, color="#FF4CAF50")
text("PC connected", 800, 600, 120, 30, GREY)
ctl("ROOM_MODE", "InCallLed", 920, 600, 30, 30, color="#FF4CAF50")
text("In a call", 960, 600, 100, 30, GREY)
ctl("ROOM_MODE", "CallStatus", 760, 640, 300, 34, style="Text", color="#FF2B2D33", text_color=WHITE, font_size=14, corner_radius=6)

spec = {
    "description": "Boardroom, 8 x 6 m, 10-12 people: 2 ceiling mics with AEC, Teams/Zoom PC on USB (audio + HID "
                   "mute sync), laptop input, 4 ceiling speakers, touch panel.",
    "core": {"model": "Core 8 Flex"},
    "usb_sound_cards": 1,
    "pages": ["Boardroom Audio"],
    "components": C,
    "wires": W,
    "ucis": [{"name": "Boardroom Touch Panel", "pages": [{"name": "Room Control", "color": "#FF1C1D21", "elements": E}]}],
}
out = Path(__file__).with_name("boardroom.json")
out.write_text(json.dumps(spec, indent=1), encoding="utf-8")
print(f"{out}: {len(C)} components, {len(W)} wires, {len(E)} UCI elements")
