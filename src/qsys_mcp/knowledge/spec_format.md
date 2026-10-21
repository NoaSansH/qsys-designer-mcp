# Design spec format

`build_design` takes a JSON spec and writes a new `.qsys` file through Designer's own libraries. No Designer window is needed. `examples/boardroom.json` in the repo is a complete worked example.

```json
{
  "core": {"model": "Core 110f", "name": "Core-1"},
  "usb_sound_cards": 1,
  "inventory": [{"type": "SoftDanteInput", "name": "Stage-Dante"}],
  "pages": ["Audio", "Control"],
  "components": [ ... ],
  "wires": [ ["A.out.1", "B.in.1"], ... ],
  "ucis": [ ... ]
}
```

## core, usb_sound_cards, inventory

- `core.model`: optional. Use a name from `designer_versions` → `core_models`, e.g. `Core110f`. Spaces and case don't matter. The default is Designer's default core (Core 8 Flex).
- `usb_sound_cards`: optional. The number of USB audio bridge slots on the Core (each is 2 in / 2 out). Each one adds two placeables, `USB Sound Card N In` (audio from the PC) and `USB Sound Card N Out` (audio to the PC).
- `inventory`: optional extra inventory items. `type` is a class name from `designer_versions` → `inventory_types`. `name` names the item, and `set` sets public properties (e.g. `{"Count": 16}`). Check what an item offers with `probe_components(inventory=[...], include_placeables=true)`.
  - Items built from a model need `model`, e.g. a camera: `{"type": "OnVifCamera", "model": "NC12x80", "name": "CAM-1"}`.
  - Inventory names follow Designer's hostname rules: letters, digits and `-`. Designer drops anything else (an `_` disappears), and the build reports the rename. Component code names can still use `_`.
- `core.usb_bridge` and an item's `usb_bridge` (I/O-USB Bridge...): settings of the USB bridge, by property name, e.g. `{"USBSlotType": "Speakerphone", "SpeakerphoneMode": "WithEchoCancel", "VideoBridge": true, "BridgeName": "Room-1", "ZoomRoom": false, "VideoFormat": "UCC"}`. With a slot type set, the item offers `USB EC Speakerphone 1 In` / `Out` (or the soundcard blocks), and `VideoBridge` adds `USB Video Bridge`.
- `design_properties`: File > Design Properties, by property name, e.g. `{"PtpPriority1": 127, "QosPreset": "Audinate", "SoftwareDanteLatency": "Dn2000"}`.

## components

Each component needs exactly one of `class` or `inventory`.

| key | meaning |
|---|---|
| `name` | Required and unique. Becomes the code name (what Lua, QRC and the checks use) and the block's label. Use letters, digits, `_` and `-`, starting with a letter. |
| `class` | Component class, e.g. `gain`, `mixer`, `acoustic_echo_canceler_simd`. Use `probe_components` to see a class's properties, pins and controls before using it. |
| `inventory` | A placeable from the Core or an inventory item: `"Flex In"`, `"USB Sound Card 1 Out"`, `"HID Conferencing"`, or `{"item": "Stage-Dante", "placeable": "..."}`. |
| `page` | Schematic page (from `pages`). Defaults to the first. Wires can't cross pages. |
| `x`, `y` | Position on the page. Lay out left to right in signal order, roughly 200 px apart. |
| `label` | Optional block label (defaults to `name`). |
| `properties` | Design-time properties by code name, e.g. `{"multi_channel_type": 3, "multi_channel_count": 4}`. Set these before relying on pin counts. |
| `script_access` | `None` (default), `Script` (Lua only), `External` (QRC/third party and UCIs over the network) or `All`. Keep it minimal. `emulate_design` makes a test copy with everything set to `All`, so access doesn't need opening up for testing. |
| `values` | Stored control values: `{"gain": -6, "mute": true, "frequency": 100}`. Numbers, booleans or strings. Use `{"value": v, "position": p}` to give the 0..1 position yourself. |
| `text_controller` | For `device_controller_script` only: `{"controls": [...], "lua": "..."}`, or `"lua_file"` relative to `spec_dir`. |
| `control_pins` | Control ids to expose as control pins (Properties > Control Pins), e.g. `["connected", "active"]` on a USB Speakerphone In, `["camera_status"]` on a camera. Wire them by pin name or by control id: `"USB_IN.out.connected"`. |
| `plugin` | Instead of `class`: an installed schematic plugin, by its name (`"Category~Name"` or just `"Name"`) or id. It's looked for in Designer's Plugins and Assets folders under Documents\QSC. Encrypted plugins installed by the Asset Manager (`.qplugx2`) go by their asset name, e.g. `"Shure Microflex Advance"`. `plugin_file` takes a `.qplug` path instead. The plugin's code is saved inside the design. |

`probe_components` lists each class's `property_choices`, so you can see what a numbered property means (for example `bandwidth_q_factor`: 0 Bandwidth, 1 Q-Factor).

### Stored values and positions

The engine applies a stored control's normalised **position** (0..1) when the design starts, not its value. The builder fills positions in from, in order of preference:

1. a `position` you give;
2. the calibration cache (positions measured on the engine by `calibrate_values`);
3. rules: exact for booleans and integers; measured for gain blocks, Flex preamp/sensitivity, filter frequency, dynamics ratio, limiter threshold and mixer crosspoint −6 dB; **assumed** for anything else.

`build_design` lists any value with an assumed taper under `unconfirmed_values`. Run `calibrate_values` on the file to measure and fix those, or at least check them over QRC after emulating.

Some controls are linked. Flex inputs, for example, have `preamp_gain` and `sensitivity` (sensitivity = 24 dBu − gain), and the engine restores sensitivity at startup, so store both. `calibrate_values` reports any value that is right when set live but wrong after a restart.

### Text Controller controls

```json
{"name": "MeetingMode", "type": "button", "button_type": "toggle"}
{"name": "RoomOff", "type": "button", "button_type": "trigger"}
{"name": "MasterVolume", "type": "knob", "unit": "dB", "min": -40, "max": 0}
{"name": "MicMutedLed", "type": "led"}
{"name": "RoomMode", "type": "status_text"}
{"name": "Notes", "type": "text"}
```

- `button_type`: `toggle`, `momentary`, `trigger` or `state_trigger`.
- `unit`: `dB`, `Hz`, `Float`, `Integer`, `Pan`, `Percent`, `Position` or `Seconds`.
- Optional on any control: `count` (makes an array, e.g. `Controls.Source[1..n]`) and `pins` (`None`, `Input`, `Output` or `Both`).

In Lua, a control with count 1 is `Controls.Name`. Other blocks are reached with `Component.New("CODE_NAME")`, which needs that block's `script_access` to be `Script` or `All`. The Lua is syntax-checked before building.

## wires

Write each wire as `["FROM.out.N", "TO.in.M"]`. A pin can be given by its 1-based number or by its name, for example `"AEC_01.in.Reference"` or `"CONF_AUTOMIXER.out.Mix Output"`. `probe_components` lists each pin's number and name.

- One output can feed many inputs.
- Each input takes one wire.
- Both ends must be on the same page.
- A third element names the signal: `["MIX.out.1", "GAIN.in.1", "HP Left"]`, or `{"from", "to", "signal"}`. The name goes on the source pin, so Designer shows it on every wire leaving that pin. Give each output pin one name, and don't reuse a name on another output.

## signals

Connections by signal name, with no wire drawn, for pins that are far apart (or on different pages): Designer joins an output and the inputs that carry the same name.

```json
"signals": [["USB_IN.out.connected", "STATE.in.UsbConnected", "USB connected"]]
```

`{"from", "to", "signal"}` works too. The name goes on both pins; one output can feed several inputs by name, and an input fed by name can't also have a wire. `read_design` lists these under `signal_links` and counts them as connections in `check_design`.

## graphics

Notes and group boxes on the schematic:

```json
{"type": "group", "text": "INPUTS", "x": -40, "y": -90, "w": 330, "h": 640, "fill": "#40D32F2F", "bold": true, "font_size": 13},
{"type": "text", "text": "Ceiling mic, AEC on", "x": 0, "y": 60, "w": 170, "h": 30, "font_size": 9, "align": "Left", "fill": "#FFFFF4C2"}
```

- `type`: `text` (a text block, transparent unless `fill` is set) or `group` (a titled group box).
- Optional: `page`, `text_color`, `stroke_color`, `font`, `font_size`, `bold`, `align` (`Left`, `Center`, `Right`), `valign`, `corner_radius`, `stroke_width`, `z`.
- Group boxes are drawn behind the blocks (z −100) unless `z` says otherwise.

## snapshot_banks

```json
{"name": "Room_Modes", "count": 6, "controls": [
   {"component": "MIX", "controls": ["input_1_output_1_mute", "input_2_output_1_mute"]},
   {"component": "MIC_MUTE", "controls": ["mute"]},
   {"component": "LEVELS", "all": true}],
 "controller": {"name": "Room_Modes", "x": 900, "y": 600, "script_access": "External", "label": "Room modes"}}
```

- Creates the bank (Snapshots panel) with the listed controls, or with every control of a component (`all`).
- `controller` places its Snapshot Controller on the schematic under that code name, usable by Lua, QRC and UCIs (`load_1`, `save_1`...). `mode` is `Normal`, `WriteProtect` or `Scene`.
- The bank starts empty. Snapshot contents are written by the engine, not stored as control values: fill them with `record_snapshots`, which saves each snapshot in emulation and copies the result into the design. `read_design` shows which snapshots of each bank hold data.

## ucis

```json
{"name": "Touch Panel", "pages": [{"name": "Main", "color": "#FF1C1D21", "elements": [
  {"type": "text", "text": "ROOM VOLUME", "x": 30, "y": 320, "w": 300, "h": 30, "color": "#FFFFFFFF", "font_size": 18, "align": "Left"},
  {"type": "control", "component": "ROOM_MODE", "control": "MasterVolume", "x": 40, "y": 360, "w": 90, "h": 400,
   "style": "Fader", "color": "#FFD6D9DE"},
  {"type": "control", "component": "ROOM_MODE", "control": "MeetingMode", "x": 30, "y": 130, "w": 320, "h": 110,
   "label": "MEETING", "color": "#FF2E7D32", "off_color": "#FF3A3B40", "text_color": "#FFFFFFFF", "font_size": 24, "corner_radius": 10}
]}]}
```

- `style` values include `Button`, `Knob`, `Fader`, `Text`, `Meter` and `Led`. If you leave it out, the control's default style is used.
- Colours are `#AARRGGBB`.
- Elements are drawn in list order, with later ones on top.
- UCIs don't need Script Access.
- Don't put graphic boxes behind controls: Designer draws them on top.
- A Lua script can set a button's text at runtime with `Controls.X.Legend = "..."`. For that to show, leave `label` unset.

## Checking a design

Two tools check a built design:

- `read_design` reads a saved file back.
- `check_design` runs static checks on it: unique names, unconnected pins, stored positions, and signal paths. Signal paths are written as `{"from": "FLEX_IN.out.3", "to": "USB_TX_CONF", "expect": false}`, with optional `via: [...]`. An AEC's Reference input counts as subtracted, not passed through.

For control logic, use `emulate_design` and then the `qrc_*` tools. Emulation has no audio: it can test control state, but not whether sound actually gets through.
