# Design spec format

`build_design` takes a JSON spec and writes a new `.qsys` file through Designer's own libraries. No Designer window is needed. `examples/boardroom.json` in the repo is a complete worked example.

```json
{
  "core": {"model": "Core 110f", "name": "Core-1"},
  "usb_sound_cards": 1,
  "inventory": [{"type": "SoftDanteInput", "name": "Stage-Dante"}],
  "design": {"set": {"MtuValue": 1500}},
  "pages": ["Audio", "Control"],
  "components": [ ... ],
  "wires": [ ["A.out.1", "B.in.1"], ... ],
  "snapshots": [ ... ],
  "ucis": [ ... ]
}
```

## core, usb_sound_cards, inventory, design

- `core.model`: optional. Use a name from `designer_versions` → `core_models`, e.g. `Core110f`. Spaces and case don't matter. The default is Designer's default core (Core 8 Flex).
- `core.set` and `core.usb`: optional. The Core's own properties and its USB bridge (speakerphone, sound cards, video bridge). See [Hardware settings](#hardware-settings).
- `usb_sound_cards`: optional. The number of USB audio bridge slots on the Core (each is 2 in / 2 out). Each one adds two placeables, `USB Sound Card N In` (audio from the PC) and `USB Sound Card N Out` (audio to the PC). Give either this or `core.usb`, not both.
- `inventory`: optional extra inventory items. `type` is a class name from `designer_versions` → `inventory_types`. `name` names the item, `set` sets public properties (e.g. `{"Count": 16}`) and `usb` sets up a device's USB bridge (see [Hardware settings](#hardware-settings)). Check what an item offers with `probe_components(inventory=[...], include_placeables=true)`. Designer drops `_` from item names, so use `-` instead; the spec's name still works in a component's `inventory.item`.
- `design.set`: optional. The design's own properties: PTP, QoS, MTU, Software Dante. See [Hardware settings](#hardware-settings).

## Hardware settings

Some settings belong to the hardware, not to a block: the Core's USB bridge (speakerphone, sound cards, video bridge), a device's properties (a camera's model, a Software Dante channel count) and the design's network settings (PTP, QoS, MTU, Software Dante). They go in `set` and `usb` blocks.

```json
{
  "core": {"model": "Core 110f", "name": "Core-1", "set": {"Location": "Rack A"},
           "usb": {"type": "speakerphone", "mode": "WithEchoCancel", "video_bridge": true}},
  "design": {"set": {"PtpDomain": 0, "PtpPriority1": 127, "MtuValue": 1500}},
  "inventory": [
    {"type": "IoUsbBridge", "name": "IO-USB-1",
     "usb": {"type": "speakerphone", "mode": "WithEchoCancel", "video_bridge": true, "set": {"ZRMode": false}}},
    {"type": "OnVifCamera", "name": "Cam-1", "set": {"Model": "NC-12x80"}},
    {"type": "SoftDanteInput", "name": "Dante-In", "set": {"ChannelCount": "Dn8"}}
  ]
}
```

- `set` works the same on `core`, an inventory item, `design`, a `usb` block and a USB slot: public properties by name. Case, spaces and `_` don't matter. Values are numbers, booleans or strings. An enum takes a member name (`"Dn8"`), the label Designer shows (`"NC-12x80"`) or a number. An unknown name or value fails with a list of the names or choices that exist.
- `usb` goes on the Core or on an inventory item with a USB bridge (`designer_versions` → `usb_bridge_types`):
  - `type`: `speakerphone`, `soundcard` or `none`.
  - `count`: the number of USB slots. A speakerphone or sound card gets at least 1. `none` means 0.
  - `mode`: the speakerphone mode, e.g. `WithEchoCancel`, by name or number.
  - `video_bridge`: `true` adds the USB video bridge (`usb_uvc`).
  - `set`: any other USB setting, e.g. `{"ZRMode": false}`. It is tried as `USBBridgeZRMode` on the device first, then on its USB bridge, then as `ZRMode` on the device.
  - `slots`: one `set` per USB slot, in order, e.g. `[{"Location": "Lectern"}]`.
- `design.set`: the design's properties, e.g. `PtpDomain`, `PtpPriority1`, `PtpPriority2`, `QosPreset`, `MtuValue`, `SoftwareDanteInterface`, `SoftwareDanteLatency`.

The build reads every setting back (a value that doesn't stick is a warning) and checks the USB blocks: after `usb`, a speakerphone or sound card must offer a USB input and output block (`usb_receiver`, `usb_transmitter`), and a video bridge a `usb_uvc` block. If one is missing, the build fails with the device's blocks and settable properties. `build_design` lists everything it set, and each USB device's blocks, under `hardware`. Place the blocks like any other placeable, e.g. `{"inventory": {"class": "usb_receiver"}}` for the Core's speakerphone input or `{"inventory": {"item": "IO-USB-1", "class": "usb_uvc"}}`.

Property names and enum values change between Designer versions. To find them:

- `designer_versions` → `usb_bridge_types` lists the inventory types with a USB bridge.
- `probe_components(include_hardware=true, core={"model": "Core 110f", "usb": {"type": "speakerphone"}}, inventory=[...], include_placeables=true)` lists what can be set on the Core, its USB bridge and slots, and the design, with each enum's choices. Each inventory item comes back with its settable properties, and the placeables show the blocks the settings give.
- `read_design` reports a saved design's settings: `core.usb` (type, count, mode, video bridge), `core.settings`, `design.settings`, and each inventory item's `settings` and `usb`. Leave `settings` out of `sections` to skip them.

## components

Each component needs exactly one of `class` or `inventory`.

| key | meaning |
|---|---|
| `name` | Required and unique. Becomes the code name (what Lua, QRC and the checks use) and the block's label. Use letters, digits, `_` and `-`, starting with a letter. |
| `class` | Component class, e.g. `gain`, `mixer`, `acoustic_echo_canceler_simd`. Use `probe_components` to see a class's properties, pins and controls before using it. |
| `inventory` | A placeable from the Core or an inventory item: `"Flex In"`, `"USB Sound Card 1 Out"`, `"HID Conferencing"`, or `{"item": "Stage-Dante", "placeable": "..."}`. `{"item": "IO-USB-1", "class": "usb_receiver"}` takes the item's block of that class, and `{"item": "Cam-1"}` an item's only block. Without `item`, the Core is used. |
| `page` | Schematic page (from `pages`). Defaults to the first. Wires can't cross pages. |
| `x`, `y` | Position on the page. Lay out left to right in signal order, roughly 200 px apart. |
| `label` | Optional block label (defaults to `name`). |
| `properties` | Design-time properties by code name, e.g. `{"multi_channel_type": 3, "multi_channel_count": 4}`. Set these before relying on pin counts. |
| `script_access` | `None` (default), `Script` (Lua only), `External` (QRC/third party and UCIs over the network) or `All`. Keep it minimal. `emulate_design` makes a test copy with everything set to `All`, so access doesn't need opening up for testing. |
| `values` | Stored control values: `{"gain": -6, "mute": true, "frequency": 100}`. Numbers, booleans or strings. Use `{"value": v, "position": p}` to give the 0..1 position yourself. |
| `text_controller` | For `device_controller_script` only: `{"controls": [...], "lua": "..."}`, or `"lua_file"` relative to `spec_dir`. |
| `control_pins` | Controls that show a control pin, so control wires can reach them: `["connected", {"control": "mute", "dir": "both"}]`. See Control pins below. |

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

### Control pins

A control of any block can show a control pin (Properties > Control Pins in Designer), so that a control wire can join it to another control. List them in `control_pins` by control code name:

```json
{"name": "USB_IN", "inventory": "USB Sound Card 1 In", "control_pins": ["connected"]}
{"name": "PROGRAM_GAIN", "class": "gain", "control_pins": [{"control": "mute", "dir": "both"}, {"control": "gain", "dir": "in"}]}
```

- `dir` is `in`, `out` or `both`. Without it, the control gets what Designer offers: an output pin for read-only controls and indicators, both pins for the rest.
- `probe_components` shows the directions each control offers under `pin`. A class there can take `control_pins` too, e.g. `{"class": "gain", "control_pins": ["mute"]}`: the pins it would get come back without anything being written.
- A Text Controller's own controls take `pins` instead (see above).
- `build_design` lists each control pin it added under `control_pins`, with `route` saying how Designer added it.
- Control pins are numbered after the block's own pins, in the order they were added (in a build, the order of `control_pins`). Name them in wires rather than numbering them.

## wires

Write each wire as `["FROM.out.N", "TO.in.M"]`. A pin can be given by its 1-based number or by its name, for example `"AEC_01.in.Reference"` or `"CONF_AUTOMIXER.out.Mix Output"`. `probe_components` lists each pin's number and name.

Control wires are written the same way. A control pin can also be named by its control code name: `["USB_IN.out.connected", "ROOM_LOGIC.in.PcConnected"]`, `["ROOM_LOGIC.out.MuteAll", "PROGRAM_GAIN.in.mute"]`. A wire joins two control pins or two signal pins, never one of each.

- One output can feed many inputs.
- Each input takes one wire.
- Both ends must be on the same page.

## snapshots

Each entry is a snapshot bank and its Snapshot Controller block, which has `load_1`..`load_N`, `save_1`..`save_N` and `ramp_time`.

```json
"snapshots": [{
  "name": "Room_Modes",
  "count": 4,
  "members": ["MIXER.input_1_mute",
              {"component": "MIC_GAIN", "controls": ["gain", "mute"]},
              {"component": "PROGRAM_EQ", "all": true}],
  "controller": {"page": "Control", "x": 600, "y": 100, "script_access": "Script"},
  "values": {
    "1": {"MIXER.input_1_mute": false, "MIC_GAIN.gain": -6},
    "2": {"MIXER.input_1_mute": true, "MIC_GAIN.mute": true}
  }
}]
```

| key | meaning |
|---|---|
| `name` | Required and unique. The bank's name and its controller's code name, so it can't also be a component's name. Same rules as component names. |
| `count` | Required. How many snapshots the bank holds (1 or more). |
| `members` | What the bank stores: `"COMPONENT.control"`, `{"component": ..., "controls": [...]}` for some controls, or `{"component": ..., "all": true}` for every control of the block. |
| `controller` | Optional. Where the controller block goes: `page`, `x`, `y`, `label` and `script_access`, as for components (default `None`). |
| `values` | Optional stored snapshots: snapshot number → `{"COMPONENT.control": value}`. Each snapshot starts from the design's own values, so a control it doesn't list keeps the design's value, and `{}` stores the design's values as they are. Every control must be a member of the bank. |

A UCI can use the controller's buttons: `{"type": "control", "component": "Room_Modes", "control": "load_1", ...}`.

### Snapshot values

The engine writes snapshot data. It's kept in the design file, but not as design objects, so `build_design` can't always store it. A bank whose values it couldn't store reports `"values": "snapshot values need save_snapshots"`, and `next_step` says so.

Then run `save_snapshots(path, spec)`. It emulates a test copy, sets each snapshot's controls over QRC, presses the controller's `save_N`, checks every snapshot with `load_N` and puts the controls back. It then saves the copy in Designer and writes it over the file, with each block's own Script Access. Any control a script changes during that run is saved too, as when you save after emulating in Designer.

`read_design` lists each bank (members, controller block) and which snapshots hold data (`stored`).

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

- `read_design` reads a saved file back. Each pin has its `domain` (`audio`, `control`, `video`...). A control pin added for a control has `user_created` and the control's code name in `control`, and a block with such pins lists them under `control_pins`, in the spec's form. `control_wire_count` counts the wires between control pins.
- `check_design` runs static checks on it: unique names, unconnected signal pins, unused control pins, stored positions, and signal paths. Signal paths are written as `{"from": "FLEX_IN.out.3", "to": "USB_TX_CONF", "expect": false}`, with optional `via: [...]`. An AEC's Reference input counts as subtracted, not passed through.

Signal and control stay apart in the checks. A block passes its signal inputs to its signal outputs and its control inputs to its control outputs. A path rule that starts at a control pin, such as `{"from": "USB_IN.out.connected", "to": "PROGRAM_GAIN", "via": ["ROOM_LOGIC"]}`, follows control wires, and a bare block name in `to` or `via` then means that block's control pins.

Control pins don't count as unconnected pins. Instead, a control in a block's `control_pins` with none of its pins wired fails "no unused control pins"; wiring one side of a `both` is enough. `allow_unconnected` covers both checks. A block's own control pins (logic gate inputs and the like) aren't checked.

For control logic, use `emulate_design` and then the `qrc_*` tools. Emulation has no audio: it can test control state, but not whether sound actually gets through.
