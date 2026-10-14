"""Runtime (QRC) tests for the boardroom example (examples/boardroom.json).

Run while Designer is emulating the design:
    python examples/boardroom_runtime_test.py full <design-name> [report.txt]
        after emulate_design(path) - a test copy with every block visible
    python examples/boardroom_runtime_test.py production <design-name> [report.txt]
        after emulate_design(path, test_copy=False) - only ROOM_MODE is External
<design-name> is the DesignName qrc_status reports (the file name without .qsys).

Emulation has no audio, so these tests cover control state only: the ROOM_MODE state machine,
the DSP mute/gain controls it drives, stored initial values, and the USB HID mute sync
(the PC side is simulated by setting the HID LED controls over QRC).
"""
import asyncio
import sys

from qsys_mcp.qrc.client import QrcClient, QrcError

q = QrcClient()
results: list[tuple[str, str, str]] = []
SETTLE = 0.4


def record(name: str, outcome: str, detail: str = "") -> None:
    results.append((name, outcome, detail))
    print(f"{outcome} {name}" + (f"  -- {detail}" if detail else ""))


def check(name: str, ok: bool, detail: str = "") -> None:
    record(name, "PASS" if ok else "FAIL", detail)


async def get(component: str, *controls: str) -> dict:
    res = await q.get(component, list(controls))
    out = {}
    for c in res.get("Controls", []):
        out[c["Name"]] = c.get("Value")
        out[c["Name"] + "#s"] = c.get("String")
        out[c["Name"] + "#legend"] = c.get("Legend")
        out[c["Name"] + "#strings"] = c.get("Strings") or []
    return out


async def val(component: str, control: str):
    return (await get(component, control))[control]


async def press(component: str, control: str, value) -> None:
    await q.set(component, control, value)
    await asyncio.sleep(SETTLE)


async def dsp_state() -> dict:
    return {
        "mic_tx_muted": await val("MIC_MUTE", "mute"),
        "master_muted": await val("ROOM_MASTER_VOL", "mute"),
        "master_gain": round(await val("ROOM_MASTER_VOL", "gain"), 1),
        "laptop_muted": await val("LAPTOP_AUDIO_IN", "mute"),
        "conf_rx_muted": await val("CONF_RX_LEVEL", "mute"),
    }


async def ui_state() -> dict:
    r = await get("ROOM_MODE", "RoomMode", "IdleLed", "MeetingLed", "PresentationLed", "MeetingMode",
                  "PresentationMode", "MicMute", "MicMutedLed", "ProgramEnable", "ProgramLed", "MasterMute",
                  "MasterVolume", "StatusMessage", "CallStatus", "InCallLed", "PcConnectedLed")
    out = {k: v for k, v in r.items() if "#" not in k}
    for k in ("RoomMode", "StatusMessage", "CallStatus"):
        out[k + "Text"] = r[k + "#s"]
    out["MicMuteLegend"] = r["MicMute#legend"]
    return out


def leds(ui: dict) -> tuple:
    return (ui["IdleLed"], ui["MeetingLed"], ui["PresentationLed"])


async def consistent(label: str) -> None:
    d, u = await dsp_state(), await ui_state()
    ok = (bool(u["MicMute"]) == bool(d["mic_tx_muted"]) == bool(u["MicMutedLed"])
          and bool(u["ProgramEnable"]) == (not d["laptop_muted"]) == bool(u["ProgramLed"])
          and bool(u["MasterMute"]) == bool(d["master_muted"])
          and abs(u["MasterVolume"] - d["master_gain"]) < 0.2
          and sum(leds(u)) == 1)
    check(f"{label}: UI feedback matches DSP state, exactly one mode LED lit", ok, f"ui={u} dsp={d}")


async def script_log() -> str:
    """Whole script log, oldest first (log_history's String is only the newest line)."""
    lines = (await get("ROOM_MODE", "log_history"))["log_history#strings"]
    return "\n".join(reversed([x for x in lines if x]))


async def common_header(design: str) -> None:
    st = await q.status()
    check("QRC reachable and design emulating", st.get("State") == "Active" and st.get("DesignName") == design,
          f"{st.get('Platform')} {st.get('State')} {st.get('DesignName')}")
    s = await get("ROOM_MODE", "script_status", "script_error_count")
    check("ROOM_MODE Lua script running without errors", s.get("script_error_count") in (0, 0.0),
          f"status={s.get('script_status#s')} errors={s.get('script_error_count')}")


async def production() -> None:
    """Production file: only ROOM_MODE is reachable from outside. Proves Script access is enough
    for the Lua to drive its blocks (the panel feedback is read back from the DSP)."""
    await common_header(DESIGN)
    visible = sorted(c["Name"] for c in await q.components())
    check("production access: only ROOM_MODE is visible to QRC", visible == ["ROOM_MODE"], f"visible={visible}")
    u = await ui_state()
    check("startup IDLE, mics and room muted (read back through the script)",
          u["RoomModeText"] == "IDLE" and u["MicMute"] == 1 and u["MasterMute"] == 1 and abs(u["MasterVolume"] + 20) < 0.2, str(u))
    await press("ROOM_MODE", "MeetingMode", 1)
    u = await ui_state()
    check("MEETING: mics live, room unmuted", u["RoomModeText"] == "MEETING" and u["MicMute"] == 0 and u["MasterMute"] == 0, str(u))
    await press("ROOM_MODE", "VolumeUp", 1)
    check("Volume + raises the master by 2 dB", abs((await val("ROOM_MODE", "MasterVolume")) + 18) < 0.2)
    await press("ROOM_MODE", "PresentationMode", 1)
    u = await ui_state()
    check("PRESENTATION: laptop on", u["RoomModeText"] == "PRESENTATION" and u["ProgramEnable"] == 1, str(u))
    await press("ROOM_MODE", "RoomOff", 1)
    u = await ui_state()
    check("ROOM OFF: back to IDLE, everything muted, volume reset",
          u["RoomModeText"] == "IDLE" and u["MicMute"] == 1 and u["MasterMute"] == 1 and u["ProgramEnable"] == 0
          and abs(u["MasterVolume"] + 20) < 0.2, str(u))
    s = await get("ROOM_MODE", "script_error_count")
    check("no script errors after the run", s.get("script_error_count") in (0, 0.0))


async def full() -> None:
    await common_header(DESIGN)
    comps = {c["Name"]: c["Type"] for c in await q.components()}
    expected = ["MIC_01_HPF", "MIC_02_HPF", "MIC_01_EQ", "MIC_02_EQ", "AEC_01", "AEC_02", "CONF_AUTOMIXER", "CONF_TX_LEVELER",
                "CONF_TX_COMP", "MIC_MUTE", "USB_TX_CONF", "USB_RX_CONF", "USB_HID_CONF", "CONF_RX_LEVEL", "LAPTOP_AUDIO_IN",
                "ROOM_PROGRAM_MIX", "ROOM_MASTER_VOL", "SPK_OUT_01", "SPK_OUT_02", "SPK_OUT_03", "SPK_OUT_04", "SPK_HPF",
                "SPK_EQ", "SPK_LIMITER", "SPK_METER", "TX_METER", "MIC_INPUT_METER", "ROOM_MODE"]
    missing = [n for n in expected if n not in comps]
    check(f"all {len(expected)} named components present in the running design", not missing, f"missing={missing}")
    check("AEC_01/AEC_02 run as acoustic echo cancellers", comps.get("AEC_01") == comps.get("AEC_02") == "acoustic_echo_canceler_simd")

    f = await get("FLEX_IN", *[f"channel_{i}_flex_input_enable" for i in range(1, 9)], "channel_1_preamp_gain", "channel_2_preamp_gain",
                  "channel_1_phantom_power", "channel_2_phantom_power")
    fo = await get("FLEX_OUT", *[f"channel_{i}_flex_output_enable" for i in range(1, 9)])
    check("Core 8 Flex channels 1-4 inputs, 5-8 outputs",
          [f[f"channel_{i}_flex_input_enable"] for i in range(1, 9)] == [1] * 4 + [0] * 4
          and [fo[f"channel_{i}_flex_output_enable"] for i in range(1, 9)] == [0] * 4 + [1] * 4)
    check("mic preamps at 36 dB (-12 dBu sensitivity) with phantom power on", f["channel_1_preamp_gain"] == 36 and f["channel_2_preamp_gain"] == 36
          and f["channel_1_phantom_power"] == 1 and f["channel_2_phantom_power"] == 1,
          f"gain={f['channel_1_preamp_gain']}/{f['channel_2_preamp_gain']} phantom={f['channel_1_phantom_power']}/{f['channel_2_phantom_power']}")

    stored = {
        ("MIC_01_HPF", "frequency"): 100, ("MIC_02_HPF", "frequency"): 100, ("SPK_HPF", "frequency"): 80,
        ("CONF_RX_LEVEL", "gain"): -6, ("LAPTOP_AUDIO_IN", "gain"): -6,
        ("ROOM_PROGRAM_MIX", "input_1_output_1_gain"): -6, ("ROOM_PROGRAM_MIX", "input_2_output_1_gain"): -6,
        ("ROOM_PROGRAM_MIX", "input_3_output_1_gain"): -6, ("ROOM_PROGRAM_MIX", "input_4_output_1_gain"): -6,
        ("CONF_TX_COMP", "ratio"): 2, ("SPK_LIMITER", "threshold_level"): -10,
    }
    for (c, ctl), want in stored.items():
        got = await val(c, ctl)
        check(f"initial value {c}.{ctl} = {want}", got is not None and abs(got - want) < 0.05, f"got {got}")
    agc = await get("CONF_TX_LEVELER", "bypass", "target_level")
    check("CONF_TX_LEVELER (AGC) active, not bypassed", agc.get("bypass") == 0, f"target={agc.get('target_level#s')}")

    # ---- Idle ----
    d, u = await dsp_state(), await ui_state()
    check("startup state is IDLE", u["RoomModeText"] == "IDLE" and leds(u) == (1, 0, 0))
    check("IDLE: mic TX muted, master muted at -20 dB, laptop off, conf RX muted",
          d == {"mic_tx_muted": 1, "master_muted": 1, "master_gain": -20.0, "laptop_muted": 1, "conf_rx_muted": 1}, str(d))
    check("MIC MUTE button legend reads MICS MUTED", u["MicMuteLegend"] == "MICS MUTED", f"legend={u['MicMuteLegend']!r}")
    await consistent("IDLE")
    await press("ROOM_MODE", "MicMute", 0)
    check("IDLE: unmute request from panel refused", await val("MIC_MUTE", "mute") == 1 and await val("ROOM_MODE", "MicMute") == 1)
    await press("ROOM_MODE", "ProgramEnable", 1)
    check("IDLE: laptop enable refused", await val("LAPTOP_AUDIO_IN", "mute") == 1)
    await press("ROOM_MODE", "VolumeUp", 1)
    check("IDLE: volume + refused", abs(await val("ROOM_MASTER_VOL", "gain") + 20) < 0.2)
    await press("MIC_MUTE", "mute", 0)
    check("IDLE: external DSP unmute of MIC_MUTE is re-muted", await val("MIC_MUTE", "mute") == 1)

    # ---- Meeting ----
    await press("ROOM_MODE", "MeetingMode", 1)
    d, u = await dsp_state(), await ui_state()
    check("Idle -> MEETING", u["RoomModeText"] == "MEETING" and leds(u) == (0, 1, 0))
    check("MEETING: mic TX live, conf RX on, master on, laptop off",
          d["mic_tx_muted"] == 0 and d["conf_rx_muted"] == 0 and d["master_muted"] == 0 and d["laptop_muted"] == 1, str(d))
    check("MIC MUTE button legend reads MICS LIVE", u["MicMuteLegend"] == "MICS LIVE", f"legend={u['MicMuteLegend']!r}")
    await press("ROOM_MODE", "MeetingMode", 0)
    check("pressing the active mode button again keeps MEETING", (await ui_state())["RoomModeText"] == "MEETING")
    await press("ROOM_MODE", "MicMute", 1)
    check("panel MIC MUTE mutes MIC_MUTE", await val("MIC_MUTE", "mute") == 1)
    await press("ROOM_MODE", "MicMute", 0)
    await press("MIC_MUTE", "mute", 1)
    check("external DSP mute mirrored on the panel", await val("ROOM_MODE", "MicMute") == 1)
    await press("MIC_MUTE", "mute", 0)

    # volume
    await press("ROOM_MODE", "VolumeUp", 1)
    await press("ROOM_MODE", "VolumeUp", 1)
    check("Volume + twice: -20 -> -16 dB", abs(await val("ROOM_MASTER_VOL", "gain") + 16) < 0.2)
    await press("ROOM_MODE", "VolumeDown", 1)
    check("Volume - : -16 -> -18 dB", abs(await val("ROOM_MASTER_VOL", "gain") + 18) < 0.2)
    await press("ROOM_MODE", "MasterVolume", -60)
    check("fader cannot go below -40 dB (usable range)", abs(await val("ROOM_MASTER_VOL", "gain") + 40) < 0.2,
          f"gain={await val('ROOM_MASTER_VOL', 'gain')}")
    for _ in range(25):
        await q.set("ROOM_MODE", "VolumeUp", 1)
        await asyncio.sleep(0.05)
    await asyncio.sleep(SETTLE)
    check("Volume + stops at 0 dB", abs(await val("ROOM_MASTER_VOL", "gain")) < 0.2)
    await press("ROOM_MODE", "MasterVolume", -18)
    await press("ROOM_MODE", "ProgramEnable", 1)
    check("MEETING: laptop can be enabled", await val("LAPTOP_AUDIO_IN", "mute") == 0)
    await press("ROOM_MODE", "MeetingMode", 0)
    u = await ui_state()
    check("re-pressing MEETING keeps the user's laptop choice and the mode",
          await val("LAPTOP_AUDIO_IN", "mute") == 0 and u["RoomModeText"] == "MEETING" and u["MeetingMode"] == 1)
    await consistent("MEETING after user changes")

    # ---- USB HID (Teams/Zoom) mute sync; the PC side is simulated over QRC ----
    try:
        await press("USB_HID_CONF", "spk_led_connected", 1)
        await press("USB_HID_CONF", "spk_led_off_hook", 1)
        hid_settable = await val("USB_HID_CONF", "spk_led_off_hook") == 1
    except QrcError as e:
        hid_settable = False
        record("HID: simulate the PC by setting the HID LED controls", "BLOCKED", str(e))
    if hid_settable:
        u = await ui_state()
        check("HID: PC connected and in-call shown on the panel",
              u["PcConnectedLed"] == 1 and u["InCallLed"] == 1 and u["CallStatusText"] == "In call, mics live", str(u))
        await press("USB_HID_CONF", "spk_led_mute", 1)
        check("HID: muting in the app mutes the room (MIC_MUTE) and the panel",
              await val("MIC_MUTE", "mute") == 1 and await val("ROOM_MODE", "MicMute") == 1
              and (await ui_state())["CallStatusText"] == "In call, muted")
        await press("USB_HID_CONF", "spk_led_mute", 0)
        check("HID: unmuting in the app unmutes the room", await val("MIC_MUTE", "mute") == 0)
        await press("ROOM_MODE", "MicMute", 1)
        await asyncio.sleep(0.5)
        log = await script_log()
        check("HID: muting on the panel presses the app's mute button (phone mute sent to the PC)",
              await val("MIC_MUTE", "mute") == 1 and "pressing phone mute so the app matches the room (mute)" in log)
        # the simulated app now reports muted, as Teams would after the button press
        await press("USB_HID_CONF", "spk_led_mute", 1)
        await asyncio.sleep(1.6)
        check("HID: once the app confirms, room and app agree and no further presses",
              await val("MIC_MUTE", "mute") == 1 and log.count("pressing phone mute") == (await script_log()).count("pressing phone mute"))
        await press("ROOM_MODE", "MicMute", 0)
        await press("USB_HID_CONF", "spk_led_mute", 0)
        await asyncio.sleep(1.6)
    else:
        record("HID mute sync (app <-> room)", "NOT TESTED",
               "the HID LEDs are driven by the PC; in emulation QRC writes to them are accepted but ignored (stay 0), so the PC side cannot be simulated")

    # ---- Presentation, Room Off ----
    await press("ROOM_MODE", "PresentationMode", 1)
    d, u = await dsp_state(), await ui_state()
    check("Meeting -> PRESENTATION: laptop on, master on, mics still live",
          u["RoomModeText"] == "PRESENTATION" and leds(u) == (0, 0, 1) and d["laptop_muted"] == 0 and d["mic_tx_muted"] == 0, str(d))
    await consistent("PRESENTATION")
    await press("ROOM_MODE", "RoomOff", 1)
    await asyncio.sleep(0.5)
    d, u = await dsp_state(), await ui_state()
    check("ROOM OFF: IDLE, everything muted, volume reset to -20 dB",
          u["RoomModeText"] == "IDLE" and d == {"mic_tx_muted": 1, "master_muted": 1, "master_gain": -20.0, "laptop_muted": 1, "conf_rx_muted": 1}, str(d))
    log = await script_log()
    if hid_settable:
        check("HID: Room Off during a call asks the app to mute as well", log.count("(mute)") >= 2)
    else:
        presses = log.count("pressing phone mute")
        check("HID: with no call active, room mute changes never press the app's mute button", presses == 0, f"presses={presses}")
    check("display placeholder hook called (ON for modes, OFF for Room Off)",
          "DISPLAY (placeholder, no display defined): ON" in log and "DISPLAY (placeholder, no display defined): OFF" in log)
    await consistent("after ROOM OFF")
    await press("ROOM_MODE", "PresentationMode", 1)
    check("Idle -> PRESENTATION keeps mics muted", await val("MIC_MUTE", "mute") == 1 and await val("LAPTOP_AUDIO_IN", "mute") == 0)
    await press("ROOM_MODE", "RoomOff", 1)
    if hid_settable:
        await press("USB_HID_CONF", "spk_led_off_hook", 0)
    s = await get("ROOM_MODE", "script_error_count")
    check("no script errors after the run", s.get("script_error_count") in (0, 0.0))
    print("\nROOM_MODE log (tail):\n" + (await script_log())[-1200:])


DESIGN = sys.argv[2] if len(sys.argv) > 2 else ""


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    await (production() if mode == "production" else full())
    counts = {k: sum(1 for _, o, _ in results if o == k) for k in ("PASS", "FAIL", "BLOCKED", "NOT TESTED")}
    summary = "SUMMARY " + " ".join(f"{k.lower().replace(' ', '_')}={v}" for k, v in counts.items())
    print("\n" + summary)
    if len(sys.argv) > 3:
        with open(sys.argv[3], "w", encoding="utf-8") as fh:
            for n, o, det in results:
                fh.write(f"{o} {n}" + (f"  -- {det}" if det else "") + "\n")
            fh.write(summary + "\n")


asyncio.run(main())
