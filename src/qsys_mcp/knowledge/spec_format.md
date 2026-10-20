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

## components

Each component needs exactly one of `class`, `inventory` or `plugin`.

| key | meaning |
|---|---|
| `name` | Required and unique. Becomes the code name (what Lua, QRC and the checks use) and the block's label. Use letters, digits, `_` and `-`, starting with a letter. |
| `class` | Component class, e.g. `gain`, `mixer`, `acoustic_echo_canceler_simd`. Use `probe_components` to see a class's properties, pins and controls before using it. |
| `inventory` | A placeable from the Core or an inventory item: `"Flex In"`, `"USB Sound Card 1 Out"`, `"HID Conferencing"`, or `{"item": "Stage-Dante", "placeable": "..."}`. |
| `plugin` | An installed plugin, by name or id: `"My Device"`, or `{"name": "Custom~My Device", "version": "1.2"}`. See Plugins below. |
| `page` | Schematic page (from `pages`). Defaults to the first. Wires can't cross pages. |
| `x`, `y` | Position on the page. Lay out left to right in signal order, roughly 200 px apart. |
| `label` | Optional block label (defaults to `name`). |
| `properties` | Design-time properties by code name, e.g. `{"multi_channel_type": 3, "multi_channel_count": 4}`. Set these before relying on pin counts. |
| `script_access` | `None` (default), `Script` (Lua only), `External` (QRC/third party and UCIs over the network) or `All`. Keep it minimal. `emulate_design` makes a test copy with everything set to `All`, so access doesn't need opening up for testing. |
| `values` | Stored control values: `{"gain": -6, "mute": true, "frequency": 100}`. Numbers, booleans or strings. Use `{"value": v, "position": p}` to give the 0..1 position yourself. |
| `text_controller` | For `device_controller_script` only: `{"controls": [...], "lua": "..."}`, or `"lua_file"` relative to `spec_dir`. |

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

### Plugins

`plugin` places a plugin that's installed for Designer, such as a `.qplug` file in `Documents\QSC\Q-Sys Designer\Plugins`. `designer_versions` → `plugins` lists the installed ones with their name, id, version and file. A file that Designer's own plugin list leaves out is marked `"loaded": false` and can't be placed.

```json
{"name": "DEVICE_1", "plugin": "My Device", "x": 600, "y": 100,
 "properties": {"Debug Print": "All"},
 "values": {"IPAddress": "192.168.1.100", "Port": 23},
 "script_access": "External"}
```

- `plugin` is the plugin's id, or its name with or without its folder (`"Custom~My Device"` or `"My Device"`). A version at the end of the name can be left out. Case doesn't matter.
- `{"name": ..., "id": ..., "version": ...}` picks one plugin among several. If more than one version is installed and none is given, the newest is used and the build says so. `"version": "1.4"` matches 1.4.x. A name that fits two different plugins is an error: give the `id`.
- `properties` are the plugin's own properties, by the names Designer shows (they can contain spaces). They're set in spec order, and the plugin updates itself after each one, as it does in Designer, so one property can change the plugin's other properties, pins and controls. Put such a property (a model or channel count, say) first. The build warns when the plugin changes or rejects a value.
- `probe_components(plugins=[{"plugin": "My Device", "properties": {...}}])` shows a plugin's properties, pins and controls. Wires, `values` and UCI controls use those names, as for any block.
- Designer copies the plugin's code into the design, as it does when a plugin is dragged in. The plugin must be installed on the machine that builds the design. `plugin_source` and `plugin_is_encrypted` come from the installed plugin and can't be set.
- A plugin that isn't installed is an error, which lists the installed ones.
- `read_design` gives a plugin block's `class` as `%PLUGIN%_` followed by the plugin's id, and adds `plugin`: `{name, id, version, encrypted}`. The name and version come from the code saved in the design, so an encrypted plugin only shows its id. `plugin_source` is shown by its length, not its code. The design's plugins are also listed under `plugins`, with the blocks that use them.

## wires

Write each wire as `["FROM.out.N", "TO.in.M"]`. A pin can be given by its 1-based number or by its name, for example `"AEC_01.in.Reference"` or `"CONF_AUTOMIXER.out.Mix Output"`. `probe_components` lists each pin's number and name.

- One output can feed many inputs.
- Each input takes one wire.
- Both ends must be on the same page.

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
