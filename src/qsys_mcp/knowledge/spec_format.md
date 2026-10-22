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

`probe_components` lists each class's `property_choices`, so you can see what a numbered property means (for example `bandwidth_q_factor`: 0 Bandwidth, 1 Q-Factor). A list property also takes the choice's name: `"output_choice": "Mix Only"`.

A Core or device block (`inventory`) goes on the schematic once: wire everything that needs it to that one block.

Network audio set in the design rather than on site:

- A Software Dante RX block takes `"dante_subscriptions": {"1": "MXA920-123abc@Automix Out"}`: channel 1 subscribes to that device's channel (Q-SYS manages the subscription; the item's `ExternalControl` stays false). A Software Dante TX block names its channels with `values` `channel_N_label`, which is what other devices subscribe to.
- An AES67 receiver block takes `"aes67_stream": "NVX-Table"`, the stream's name as its sender announces it (SAP).
- `audit_design` warns about a Dante channel in use with no subscription and an AES67 receiver in use with no stream.

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
- The bank starts empty. Snapshot contents are written by the engine, not stored as control values: fill them with `record_snapshots`, which saves each snapshot in emulation and copies the result into the design. `read_design` shows which snapshots of each bank hold data, and `read_snapshots` what each one sets, read straight from the file (no emulation).

## ucis

```json
{"name": "Touch Panel", "width": 1280, "height": 800, "pages": [{"name": "Main", "color": "#FF1C1D21", "elements": [
  {"type": "text", "text": "ROOM VOLUME", "x": 30, "y": 320, "w": 300, "h": 30, "color": "#FFFFFFFF", "font_size": 18, "align": "Left"},
  {"type": "control", "component": "ROOM_MODE", "control": "MasterVolume", "x": 40, "y": 360, "w": 90, "h": 400,
   "style": "Fader", "color": "#FFD6D9DE"},
  {"type": "control", "component": "ROOM_MODE", "control": "MeetingMode", "x": 30, "y": 130, "w": 320, "h": 110,
   "label": "MEETING", "color": "#FF2E7D32", "off_color": "#FF3A3B40", "text_color": "#FFFFFFFF", "font_size": 24, "corner_radius": 10},
  {"type": "group_box", "text": "MODE", "x": 20, "y": 95, "w": 340, "h": 160, "font_size": 14, "bold": true, "color": "#FF9AA0A6",
   "fill": "#20FFFFFF", "stroke": "#FF3A3B40", "stroke_width": 1, "corner_radius": 8},
  {"type": "header", "text": "AUDIO", "x": 30, "y": 275, "w": 1030, "h": 30, "font_size": 16, "color": "#FF9AA0A6", "align": "Center"}
]}]}
```

- `width` and `height` set the UCI's size in pixels. Give both, or neither to keep Designer's default. When they change the size, the builder also sets the panel type to Custom, the orientation (portrait when `height` is more than `width`) and the aspect ratio that matches, e.g. 16:10 for 1280 × 800. `build_design` reports the size each UCI ended up with under `uci_sizes`.
- Element `type`s are `control` (the default), `text`, `group_box` (a frame with an optional title), `header` (a title with a line), `image` and `page_link`.
- `navigation` on the UCI shows Q-SYS's page tabs: `None`, `Left`, `Right`, `Top` or `Bottom`, with `tab_style` (`Classic` or `Chevron`), `navigation_size` (the bar's width in pixels), `navigation_font_size`, `swipe` (swiping between pages, default on) and `activity_timeout`. The bar is drawn over the page: start the page's elements after it (e.g. `x` from `navigation_size` + 20 with `Left`).
- `image`: `{"type": "image", "file": "logo.png", "x", "y", "w", "h"}`, PNG, JPG, GIF, BMP or SVG, the path relative to `spec_dir`. The picture is saved inside the design.
- `page_link`: `{"type": "page_link", "page": "Audio", "text": "Audio settings", "x", "y", "w", "h", "font_size", "color"}`, a touch area that opens another page of the same UCI.
- A button can be drawn with pictures: `image_on`, `image_off` (and `image_pressed_on`, `image_pressed_off`) take image files.
- `group_box` and `header` take `x`, `y`, `w`, `h`, `text`, `font_size`, `bold`, `color` (the text colour) and `align` (`Left`, `Center` or `Right`). A `group_box` also takes `fill`, `stroke`, `stroke_width` (whole pixels) and `corner_radius`. Leave a key out to keep Designer's default; without `text` there's no title.
- `style` values include `Button`, `Knob`, `Fader`, `Text`, `Meter` and `Led`. If you leave it out, the control's default style is used.
- Colours are `#AARRGGBB`.
- Elements are drawn in list order, with later ones on top. Group boxes are the exception: each page adds its group boxes first, so they get its lowest ZOrder and sit behind its other elements wherever they are in the list. Among themselves they keep list order, so list an outer box before the boxes inside it.
- UCIs don't need Script Access.
- Graphics drawn behind controls may still cover them: Designer has drawn graphic boxes on top of controls before. Open a page with group boxes in Designer to check it, and give a box under controls a translucent `fill` (alpha below `FF`), so the controls stay visible either way.
- A Lua script can set a button's text at runtime with `Controls.X.Legend = "..."`. For that to show, leave `label` unset.

## named_controls and global snapshots

```json
"named_controls": [{"name": "RoomVolume", "component": "ROOM_VOL", "control": "gain"}],
"global_snapshot_count": 8
```

Named Controls (Named Controls pane) are reachable by name over QRC (`qrc_named_controls`) and from Lua (`NamedControl`). `global_snapshot_count` sets how many Global Snapshots the design has.

## Templates

A template is a JSON file holding part of a spec (components, wires, signals, graphics, snapshot_banks, named_controls, ucis, plus `params` defaults). A spec includes it as often as needed:

```json
"include": [{"file": "room.json", "prefix": "R2_", "params": {"ROOM": "Room 2"}, "dx": 0, "dy": 900, "page": "Room 2"}]
```

- `${NAME}` in the template is replaced by the param's value (a string that is only `${NAME}` keeps the value's type).
- The template's blocks, banks, controllers, named controls, UCIs and signal names get the prefix; wire and signal endpoints follow, and endpoints to blocks outside the template keep their names.
- Positions move by `dx`/`dy`; `page` puts everything on that page (added to `pages`). File paths are relative to the including spec's folder (`spec_dir`); templates can include templates.
- A template block with `"shared": true` keeps its name and is placed once, however many times the template is included (a Core's Flex Out used by several zones): later copies only add their `values` and `control_pins` to it. A block of that name in the spec itself is used instead. The templates share the Core's inputs as `FLEX_IN` and its outputs as `SPK`.
- A template can also carry `inventory` (added once per name), `core` settings it needs (e.g. the USB bridge; the spec's own win), `zones` (prefixed with the include's prefix), and `description` / `param_help` for `list_templates`.

The server comes with ready-made templates, listed with their params by `list_templates` and included by name:

```json
"include": [{"template": "speaker_zone", "prefix": "Z1_", "params": {"SPK_L": 1, "SPK_R": 2}},
            {"template": "speaker_zone", "prefix": "Z2_", "params": {"SPK_L": 3, "SPK_R": 4}}]
```

| template | builds |
|---|---|
| `conference_usb_dante_mic` | PC on the Core's USB speakerphone bridge, a Dante ceiling mic that cancels echo itself, mic mute and meter, 2 x 3 matrix, volume, EQ, limiter, the final speaker feed back to the mic as its AEC reference |
| `conference_core_aec` | two Flex mics into a 2-channel AEC (reference = final speaker feed), automixer, mic mute and meter, PC on USB, 2 x 3 matrix, volume, EQ, limiter |
| `speaker_zone` | a 3-source stereo selector, zone volume, EQ and limiter to two output channels; once per zone with a prefix |
| `divisible_room` | two rooms and a movable wall: each room's source to its speakers, both to both with the wall open (a 2 x 2 matrix driven by a Text Controller, `WALL.WallOpen`); give `PREFIX` the include's prefix |
| `paging_bgm` | music in two zones, ducked while the paging mic speaks; the page goes to the zones opened on `PAGE_ROUTE`; zone volume and limiter |

Each builds with 0 Check Design errors and passes `audit_design`. Room-level values (EQ, Max Level, preamp gain) are left for the site.

## Automatic layout

Blocks can be placed for you, along the signal flow:

```json
"layout": "auto",
"zones": {"INPUTS": {"fill": "#40D32F2F", "text": "Sources around -20 dBFS"}},
"components": [{"name": "MIC", "class": "gain", "zone": "INPUTS", "note": "Ceiling mic, AEC in the mic"}, ...]
```

- `layout`: `"auto"` places every block; without it, only blocks that have no `x`/`y` are placed. `{"mode": "auto", "x": 40, "y": 40, "column_gap": 110, "row_gap": 30, "note_width": 150}` tunes it.
- Blocks go in columns by their distance from the sources over the wires and signals (loops are cut), and each column is ordered to limit crossings. Blocks of the same `zone` stay together; each zone gets a titled group box around its blocks, styled from `zones` (`fill`, `text`).
- `note` on a block puts a note under it (also with blocks you placed yourself).
- Block sizes come from what Designer actually drew for that kind of block, learned by `learn_block_sizes` and by `record_snapshots`; otherwise they're estimated from the label and the pins. Keep labels short: Designer cuts long ones.

## Exporting a design

`export_design` writes any saved design back as a spec (`spec`) with its snapshot contents (`snapshots`). `build_design` on the spec gives the same design: blocks and their positions, the properties that differ from each class's defaults, stored values with their positions, wires and signal names, links by name (`signals`), notes and zones, snapshot banks and controllers, named controls, UCIs element by element. `record_snapshots` with `snapshots` then puts the snapshot contents back. Plugin blocks keep their plugin's code (`plugin_source`), so the plugin needn't be installed; Text Controllers keep their configuration as it is (`text_controller.config`) and their Lua. Core and device blocks come back as `inventory` placeables, devices with their model and settings (`set`, `usb_bridge`), and the File > Design Properties that were changed.

## Editing a design

`edit_design` changes a saved file without rebuilding it, so everything else stays, snapshot contents included. Operations run in order:

| op | keys |
|---|---|
| `set_value` | `component`, `control`, `value` (optional `position`) |
| `set_values` | `component`, `values` {control: value} |
| `set_property` | `component`, `property`, `value`; wires to pins that disappear are removed and reported |
| `move` | `component` (or a note's `text`), `x`/`y` or `dx`/`dy` |
| `set_label`, `set_access`, `rename` | `component` and `label` / `script_access` / `name` |
| `set_lua` | `component`, `lua` |
| `add_control_pins` | `component`, `controls` |
| `add_inventory`, `set_usb_bridge`, `set_design_properties` | `item` (an inventory spec) / `item` + `settings` / `settings` |
| `add_component`, `remove_component` | `component` (a component spec / a name); removing also drops its wires and snapshot entries |
| `add_wire`, `remove_wire` | `from`, `to` (and `signal` to name it) |
| `add_signal`, `rename_signal`, `remove_signal` | `from`, `to`, `signal` / `signal`, `name` / `pin` or `signal` |
| `add_graphic`, `set_graphic`, `remove_graphic` | `graphic` (as in `graphics`) / `text` + `set` / `text` |
| `add_snapshot_bank` | `bank` (as in `snapshot_banks`); fill it with `record_snapshots` |
| `add_uci`, `remove_uci` | `uci` (as in `ucis`) / `name` |
| `add_named_control`, `remove_named_control` | `name`, `component`, `control` / `name` |
| `set_global_snapshot_count` | `count` |
| `rename_inventory` | `item` (its current name, the Core's too), `name`: the name Designer looks for on the network |
| `set_uci_element` | `uci`, `page` (optional), `match` (`{"text": ...}` or `{"component", "control"}`, exactly one element), `set` (`x`, `y`, `w`, `h`, `text`, `font_size`) |

Plugin blocks keep working in a saved design: their code is in the file, so their controls are available to `set_value` without the plugin being installed.

## Checking a design

Two tools check a built design:

- `read_design` reads a saved file back.
- `check_design` runs static checks on it: unique names, unconnected pins, stored positions, and signal paths. Signal paths are written as `{"from": "FLEX_IN.out.3", "to": "USB_TX_CONF", "expect": false}`, with optional `via: [...]`. An AEC's Reference input counts as subtracted, not passed through.

Other checks and documents:

- `check_contract` checks what a control system or a script expects of the design: blocks by code name, Script Access (`qrc`, `script` or `both`) and the controls read or written. `list_controls` lists every control of the blocks.
- `check_simpl` builds that contract from a Crestron SIMPL program (`.smw`) and its Q-SYS modules' `.usp` files (QscQsys library: Fader, Meter, Camera, Snapshot Controller, Generic Component Control), then checks it: each module instance's component name, and the controls it uses (for a camera, only the joins wired in the program). Other modules can be described with `modules`.
- `audit_design` looks for what makes a room unusable: a mic reaching the speakers (feedback), a PC getting its own audio back, an AEC reference taken before the room volume, blocks with no connection, limiter thresholds above 0 dBFS, high stored gains, blocks open to QRC. Paths follow the wires and signal names through each block in its state (muted or -100 dB crosspoints, muted gains), in the stored values and in every snapshot. It also checks the gain structure: a nominal -20 dBFS (or `roles.levels` per source) is carried through gains, mixer gains and limiters, summed where sources meet; clipping, little headroom (above -6 dBFS) and levels lost in the noise (below -45 dBFS) at outputs are reported, with each output's level per state in `output_levels`. `roles` names the mics, speakers, PC links, AEC reference and volume when the inferred ones aren't right.
- `document_design` writes an HTML document of the design (inventory, blocks and settings, wires and signal names, snapshot banks and, with `with_snapshot_values`, what each snapshot sets, UCIs, notes). `compare_designs` lists what changed between two versions, including what each snapshot sets.
- `read_snapshots` gives what every saved snapshot sets, read from the file without Designer: `{bank: {"1": {"BLOCK.control": value}}}`.
- `make_test_uci` adds a test UCI generated from the design: snapshot buttons, meters, gains and mutes, Text Controller states.
- `make_room_uci` adds the UCI users touch: Home (room modes from a snapshot bank, mic mute, volume), Audio (each volume) and Camera (presets from a second bank, privacy), with page tabs and an optional logo. Labels for the modes and presets go in `roles` (`{"modes": {"labels": [...]}, "camera": {"labels": [...]}}`); blocks it can't find are named there too.
- `lua_test` runs a Text Controller's script, or a plugin (`plugin_file`), outside Designer against simulated Controls, other blocks, timers, TcpSocket / UdpSocket (steps `socket` / `udp` deliver the device's data, `expect` `sent` checks what was written), HttpClient (replies given in `http`), rapidjson / json, System, Notifications and Uci.

`designer_check` runs Designer's own Check Design and returns its errors, warnings and the licences the design needs.

On a real Core (after `qrc_connect`): `compare_with_core` says which design it runs and compares every stored value with it; `pull_core_snapshots` reads the snapshots saved on site (recalling each one, then putting the controls back) and, with `out_path`, records them into a copy of the design. Recalling snapshots changes a live room, so it needs `allow_changes`. `send_to_core` and `load_from_core` drive Designer's File > Save to Core & Run and Load from Core & Connect, only with `confirm=True`; Designer finds the Core by the design's Core name.

For control logic, write a `run_scenario`: it emulates a test copy and runs steps such as `{"load": {"controller": "MODES", "snapshot": 3}}` then `{"expect": {"component": "MIC_MUTE", "control": "mute", "value": false}}`, and reports each expectation. `emulate_design` and the `qrc_*` tools are there for exploring by hand. Emulation has no audio: it can test control state, but not whether sound actually gets through.
