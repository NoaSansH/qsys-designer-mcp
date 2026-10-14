# Changelog

## 0.1.0 (2026-10-13)

First public release.

- Headless design building through Designer's own libraries: a JSON spec in, a `.qsys` file out, no Designer window. Handles components, properties, Core and inventory I/O (Flex, Mic/Line, USB audio and HID, Software Dante, AES67), wires, stored control values, Text Controllers with Lua, multi-page schematics and multi-page UCIs.
- `read_design` and `check_design` read any saved design back and check names, unconnected pins, stored values and signal paths over the real wiring.
- `emulate_design` runs a test copy in Designer's emulator. The `qrc_*` tools then test it live.
- `calibrate_values` measures stored values on the engine, fixes their stored positions and caches the measurements per Designer version.
- Works with Designer 9.x (.NET Framework, helper compiled on first use) and 10.x (.NET 8, prebuilt helper). Tested with 9.13.1 and 10.3.0.
- The Designer app is driven through UI Automation patterns only. No mouse or keyboard input is injected.
- Removed: the earlier GUI-editing tools (canvas wiring, property panes, UCI editing through the GUI).
