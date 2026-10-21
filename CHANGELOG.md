# Changelog

## 0.4.0 (2026-10-21)

- `check_contract` and `list_controls`: what a control system or script expects of a design (blocks, Script Access, controls read or written), with near-miss suggestions.
- `document_design`: an HTML document of a design, optionally with what every snapshot sets (read in emulation). `compare_designs`: what changed between two versions.
- `make_test_uci`: a test UCI generated from the design (snapshot buttons, meters, gains and mutes, Text Controller states); `edit_design` gains `add_uci` and `remove_uci`.
- `lua_test`: Text Controller scripts run outside Designer in a real Lua interpreter, against simulated Controls, Component.New, Timer (simulated clock), NamedControl and print.
- Named Controls (`named_controls`, edit ops) and the number of Global Snapshots (`global_snapshot_count`).
- Templates: `include` merges spec fragments with a prefix, parameters and an offset.
- Real Core: `qrc_connect` and `save_snapshot_live`; changes to a real Core need `allow_changes`, and emulation tools always use the local emulator.
- The .NET 8 helper for Designer 10 is rebuilt with every addition since 0.1.

## 0.3.0 (2026-10-21)

- `edit_design`: changes a saved design operation by operation (values, properties, moves, blocks, wires, signal names, notes, snapshot banks) without rebuilding it, so snapshot contents and UCIs stay.
- `designer_check`: Designer's own Check Design, with its errors, warnings and licences.
- `run_scenario`: scripted tests in emulation (set, recall a snapshot, wait, expect), reported step by step.
- Automatic layout (spec `layout`, `zone`, `note`): blocks in columns along the signal flow, zone frames and notes; block sizes learned from Designer (`learn_block_sizes`, `record_snapshots`).
- `screenshot` can open a schematic page or a UCI first, fitted to the window. Clicks and screenshots are refused unless Designer is the window in front.
- UCIs: size, group boxes and headers, taken from the outil-v0.2-distante branch with their tests.
- A loaded design's plugin blocks get their controls back from the plugin code saved in the file (reading, editing, patching values).
- Emulation waits for "Compiling Design" and acknowledges the licence notice in either order.

## 0.2.0 (2026-10-20)

- Spec: USB bridge settings of the Core and of I/O-USB Bridges (`usb_bridge`: Speakerphone, EC mode, video bridge, bridge name), inventory items built from a model (cameras: `model`), and `design_properties` (PTP, QoS, Software Dante).
- Spec: control pins on any block (`control_pins`), wired by pin name or control id.
- Spec: installed schematic plugins (`plugin`, or `plugin_file` for a `.qplug`). The plugin's code is saved in the design.
- Spec: signal names on wires (a wire's third element), and connections by signal name with no wire (`signals`).
- Spec: notes and group boxes on the schematic (`graphics`).
- Spec: snapshot banks with their Snapshot Controllers (`snapshot_banks`). New tool `record_snapshots` saves each snapshot's contents in emulation and copies them into the design.
- `read_design` returns signal names, signal-name connections, notes, snapshot banks (and which snapshots hold data) and block sizes. Snapshot Controllers are read like any other block.
- `probe_components` explains list properties (`property_choices`).
- The prebuilt .NET 8 helper (Designer 10) isn't rebuilt with these additions yet: `dotnet publish helper-net8 -c Release -o src/qsys_mcp/helper/net8`. Tested with Designer 9.13.2.

## 0.1.0 (2026-10-13)

First public release.

- Headless design building through Designer's own libraries: a JSON spec in, a `.qsys` file out, no Designer window. Handles components, properties, Core and inventory I/O (Flex, Mic/Line, USB audio and HID, Software Dante, AES67), wires, stored control values, Text Controllers with Lua, multi-page schematics and multi-page UCIs.
- `read_design` and `check_design` read any saved design back and check names, unconnected pins, stored values and signal paths over the real wiring.
- `emulate_design` runs a test copy in Designer's emulator. The `qrc_*` tools then test it live.
- `calibrate_values` measures stored values on the engine, fixes their stored positions and caches the measurements per Designer version.
- Works with Designer 9.x (.NET Framework, helper compiled on first use) and 10.x (.NET 8, prebuilt helper). Tested with 9.13.1 and 10.3.0.
- The Designer app is driven through UI Automation patterns only. No mouse or keyboard input is injected.
- Removed: the earlier GUI-editing tools (canvas wiring, property panes, UCI editing through the GUI).
