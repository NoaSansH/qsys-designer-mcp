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
- Element `type`s are `control` (the default), `text`, `group_box` (a frame with an optional title) and `header` (a title with a line).
- `group_box` and `header` take `x`, `y`, `w`, `h`, `text`, `font_size`, `bold`, `color` (the text colour) and `align` (`Left`, `Center` or `Right`). A `group_box` also takes `fill`, `stroke`, `stroke_width` (whole pixels) and `corner_radius`. Leave a key out to keep Designer's default; without `text` there's no title.
- `style` values include `Button`, `Knob`, `Fader`, `Text`, `Meter` and `Led`. If you leave it out, the control's default style is used.
- Colours are `#AARRGGBB`.
- Elements are drawn in list order, with later ones on top. Group boxes are the exception: each page adds its group boxes first, so they get its lowest ZOrder and sit behind its other elements wherever they are in the list. Among themselves they keep list order, so list an outer box before the boxes inside it.
- UCIs don't need Script Access.
- Graphics drawn behind controls may still cover them: Designer has drawn graphic boxes on top of controls before. Open a page with group boxes in Designer to check it, and give a box under controls a translucent `fill` (alpha below `FF`), so the controls stay visible either way.
- A Lua script can set a button's text at runtime with `Controls.X.Legend = "..."`. For that to show, leave `label` unset.

## Checking a design

Two tools check a built design:

- `read_design` reads a saved file back. For each UCI it reports the `width` and `height`, with Designer's names for its `aspect_ratio`, `orientation` and `panel_type`. For each UCI element it reports Designer's type (`ControlStyle`, `TextBlock`, `GroupBox` or `Header`), position, size, `z` order and `label` (a control's label, or the text of a text, group box or header), plus a group box's or header's styling under the spec's keys. If `sections` leaves out `ucis`, it lists only each UCI's name, size, pages and number of elements.
- `check_design` runs static checks on it: unique names, unconnected pins, stored positions, and signal paths. Signal paths are written as `{"from": "FLEX_IN.out.3", "to": "USB_TX_CONF", "expect": false}`, with optional `via: [...]`. An AEC's Reference input counts as subtracted, not passed through.

For control logic, use `emulate_design` and then the `qrc_*` tools. Emulation has no audio: it can test control state, but not whether sound actually gets through.
