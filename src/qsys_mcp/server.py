"""MCP server for Q-SYS Designer (unofficial).

Designs are built and read headlessly through Designer's own libraries: a JSON spec in, a .qsys
file out, no Designer window involved. The Designer app is only used to run designs: open,
emulate, stop, answer dialogs and take screenshots. The qrc_* tools then test the running design.
"""

from __future__ import annotations

import functools
import os
from importlib import resources
from typing import Any

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from qsys_mcp import designs, headless, lua, runtime
from qsys_mcp.app import AppError, Designer
from qsys_mcp.qrc.client import QrcClient, QrcError

mcp = MCPServer(
    "qsys-designer",
    instructions=(
        "Unofficial tools for building and testing Q-SYS designs. Workflow: read qsys://spec-format, "
        "use probe_components to learn the classes you need (properties, pins, controls), write a spec, "
        "build_design to a NEW file, read_design + check_design to verify what was saved, then "
        "emulate_design and the qrc_* tools to test control logic. calibrate_values measures stored "
        "values on the engine. Never overwrite an existing design unless the user asked for that. "
        "Emulation has no audio: report signal paths as statically verified, not as heard. "
        "Read qsys://lua-api before writing Lua and qsys://design-guide before reviewing a design."
    ),
)

designer = Designer()
qrc = QrcClient(
    host=os.environ.get("QSYS_QRC_HOST", "127.0.0.1"),
    port=int(os.environ.get("QSYS_QRC_PORT", "1710")),
    user=os.environ.get("QSYS_QRC_USER"),
    password=os.environ.get("QSYS_QRC_PASSWORD"),
)

EXPECTED = (AppError, QrcError, headless.HelperError, designs.SpecError, ValueError, FileExistsError, FileNotFoundError)

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITES_FILE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
APP = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=False)
APP_SAFE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
LIVE_READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True)
LIVE_WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=True)


def tool(annotations: ToolAnnotations, *, image: bool = False):
    """Register a tool; expected errors become ToolErrors so their message reaches the client."""

    def deco(fn):
        if fn.__code__.co_flags & 0x80:  # async
            @functools.wraps(fn)
            async def wrapper(*a, **kw):
                try:
                    return await fn(*a, **kw)
                except EXPECTED as e:
                    raise ToolError(str(e)) from e
        else:
            @functools.wraps(fn)
            def wrapper(*a, **kw):
                try:
                    return fn(*a, **kw)
                except EXPECTED as e:
                    raise ToolError(str(e)) from e
        return mcp.tool(annotations=annotations, structured_output=False if image else None)(wrapper)

    return deco


# ---- Designer installs and component classes ---------------------------------------------

@tool(READ_ONLY)
def designer_versions(version: str | None = None) -> dict:
    """Installed Q-SYS Designer versions (newest first), and for the chosen one (default: newest):
    the core models and inventory item types it knows."""
    found = headless.installs()
    if not found:
        raise headless.HelperError("No Q-SYS Designer installation found (set QSYS_DESIGNER_DIR if it's somewhere unusual).")
    info = headless.run({"command": "info"}, headless.pick(version))
    return {"installed": [{"version": i.version, "folder": str(i.folder), "runtime": ".NET" if headless.is_dotnet_core(i) else ".NET Framework"}
                          for i in found], **info}


@tool(READ_ONLY)
def probe_components(classes: list[dict] | None = None, core_model: str | None = None, usb_sound_cards: int | None = None,
                     inventory: list[dict] | None = None, include_placeables: bool = False, include_controls: bool = True,
                     designer_version: str | None = None) -> dict:
    """Learn component classes before using them. classes: [{"class": "mixer", "properties": {"n_inputs": 4}}]
    returns each class's resolved properties, pins (number + name) and controls (id, type, min/max).
    Unknown class names come back with an error, which is a safe way to test a name.
    include_placeables lists the Core's (and any inventory items') I/O blocks, e.g. 'Flex In',
    'USB Sound Card 1 In', with their pins. Nothing is written."""
    req: dict[str, Any] = {"command": "probe", "classes": classes or [], "placeables": include_placeables,
                           "controls": include_controls}
    if core_model:
        req["core_model"] = core_model
    if usb_sound_cards is not None:
        req["usb_sound_cards"] = usb_sound_cards
    if inventory:
        req["inventory"] = inventory
    out = headless.run(req, headless.pick(designer_version))
    if not include_controls:
        for c in out.get("classes", []):
            c.pop("controls", None)
    return out


# ---- Building and reading designs ----------------------------------------------------------

@tool(WRITES_FILE)
def build_design(spec: dict, path: str, overwrite: bool = False, designer_version: str | None = None,
                 spec_dir: str | None = None) -> dict:
    """Build a .qsys file from a spec (see qsys://spec-format), headlessly. Refuses to replace an
    existing file unless overwrite=True; only do that when the user asked for it. spec_dir is where
    relative lua_file paths are found. designer_version picks an install (e.g. '10.3'); default is
    the newest. Returns counts, warnings and any stored values whose taper is assumed."""
    return designs.build(spec, path, overwrite, designer_version, spec_dir)


@tool(WRITES_FILE)
def edit_design(path: str, ops: list[dict], out_path: str | None = None, spec_dir: str | None = None) -> dict:
    """Change a saved design without rebuilding it: everything the operations don't touch is kept,
    snapshot contents, UCIs and positions included. ops run in order, e.g.
    [{"op": "set_value", "component": "MIC", "control": "IPAddress", "value": "10.0.0.20"},
     {"op": "move", "component": "MIX", "x": 400, "y": 60},
     {"op": "add_wire", "from": "A.out.1", "to": "B.in.1", "signal": "Program L"},
     {"op": "remove_component", "component": "OLD_GAIN"}].
    All ops: see qsys://spec-format, "Editing a design". Writes `path` in place unless out_path (a new
    file) is given; edit in place only when the user asked for that file to change."""
    return designs.edit(path, ops, out_path, spec_dir)


@tool(READ_ONLY)
def read_design(path: str, components: list[str] | None = None, sections: list[str] | None = None) -> dict:
    """Read a saved .qsys back: core, USB, inventory, pages, every component (class, name, Script Access,
    properties, stored values with positions, pins and their signal names), wires, UCIs, schematic
    notes and group boxes, and snapshot banks (contents, controller, which snapshots hold data). Uses
    the Designer version that saved the file. Filter with components=[names] and sections (any of
    properties, values, pins, wires, ucis, graphics) for big designs. Works on any design, not only
    ones built here."""
    d = designs.read(path)
    return designs.summarise(d, components, set(sections) if sections else None)


@tool(READ_ONLY)
def check_design(path: str, paths: list[dict] | None = None, allow_unconnected: list[str] | None = None) -> dict:
    """Static checks on a saved design: unique names, unconnected pins, stored values have positions,
    and signal-path rules over the actual wiring, e.g.
    {"from": "FLEX_IN.out.3", "to": "USB_TX_CONF", "expect": false, "via": [...], "name": "..."}.
    'to' can be a component or a pin. An AEC's Reference input counts as subtracted, not passed on.
    allow_unconnected lists pins or components whose spare pins are intended."""
    return designs.check(designs.read(path), paths, allow_unconnected)


# ---- Running designs in Designer ----------------------------------------------------------

@tool(READ_ONLY)
def designer_status() -> dict:
    """Is Designer running, which design is open (and with which version), unsaved changes, and
    whether it's emulating. Lists any other Designer windows."""
    return designer.status()


@tool(APP)
def open_design(path: str, discard_changes: bool = False) -> dict:
    """Open a .qsys file in the Designer version that saved it. Any Designer already running is closed
    first (emulation is stopped), so only one design is ever open. If an open design has unsaved changes,
    nothing is closed and this fails, unless discard_changes=True: only pass that when the user said the
    changes don't matter. Stopping emulation counts as a change (Designer records the run's values)."""
    return designer.open_design(path, discard_changes)


@tool(APP)
def close_design(discard_changes: bool = False) -> dict:
    """Close every running Designer (stopping emulation first). Fails without closing anything if a design
    has unsaved changes, unless discard_changes=True (only when the user agreed)."""
    return {"closed": designer.close_designs(discard_changes)}


@tool(APP)
async def emulate_design(path: str, test_copy: bool = True, discard_changes: bool = False) -> dict:
    """Open a design and start Designer's emulator, ready for the qrc_* tools. By default this emulates a
    test copy (in the cache folder) with every block's Script Access set to All, so QRC can see everything;
    the original file is not touched. test_copy=False emulates the file itself, with its real access
    settings. Closes any running Designer first (see open_design for discard_changes)."""
    return await runtime.emulate(designer, qrc, path, test_copy, discard_changes)


@tool(APP_SAFE)
def stop_emulation() -> dict:
    """Stop emulating (File > Disconnect)."""
    return designer.stop()


@tool(WRITES_FILE)
async def calibrate_values(path: str, discard_changes: bool = False) -> dict:
    """Measure every stored numeric value on the engine and fix its stored position in the file
    (positions are what the engine applies at startup). Emulates a test copy, sets each value over QRC,
    reads the engine's position, patches the original file, then restarts on the patched file and
    confirms each value comes up as stored, reporting any linked controls. Measured positions are cached
    for future builds. Changes the file's stored positions only; closes any running Designer first."""
    return await runtime.calibrate(designer, qrc, path, discard_changes)


@tool(WRITES_FILE)
async def record_snapshots(path: str, snapshots: dict, discard_changes: bool = False) -> dict:
    """Fill the snapshot banks of a design (built with spec.snapshot_banks). Snapshot contents are written
    by the engine, so this emulates a test copy, sets the controls of each snapshot over QRC, presses the
    bank's Save button, saves the copy and copies the banks' saved data into `path`. snapshots:
    {"BANK": {"controller": "CODE_NAME", "snapshots": {"1": [{"component", "control", "value"}], ...}}}.
    Only the listed banks change in `path`; closes any running Designer first."""
    return await runtime.record_snapshots(designer, qrc, path, snapshots, discard_changes)


@tool(APP)
async def run_scenario(path: str, steps: list[dict], discard_changes: bool = False, keep_running: bool = False) -> dict:
    """Test a design's logic in emulation with a scripted scenario, on a test copy (the file isn't
    changed). Steps run in order, each with one action:
    {"set": {"component", "control", "value"}}, {"load": {"controller", "snapshot": 3}}, {"wait": 1.5},
    {"expect": {"component", "control", "value" or "string", "tolerance"?}}, plus an optional "label".
    Returns every expectation with what the engine had, and stops emulating unless keep_running.
    Emulation has no audio: it tests control state, not sound."""
    return await runtime.run_scenario(designer, qrc, path, steps, discard_changes, keep_running)


@tool(APP_SAFE)
async def learn_block_sizes(path: str, discard_changes: bool = False) -> dict:
    """Let Designer draw a design once (on a test copy) and remember the real size of each kind of
    block, so automatic layout (spec "layout") spaces blocks exactly. Closes any running Designer first."""
    return await runtime.learn_block_sizes(designer, qrc, path, discard_changes)


@tool(READ_ONLY)
def list_dialogs() -> list[dict]:
    """Dialogs Designer has open (title, text, buttons), e.g. after emulate_design."""
    return designer.dialogs()


@tool(APP)
def dialog_button(button: str) -> str:
    """Press a button in Designer's open dialog ('OK', 'Yes', 'Cancel', "Don't Save" ...)."""
    designer.press(button)
    return f"Pressed {button!r}"


@tool(READ_ONLY, image=True)
def screenshot(view: str | None = None, name: str | None = None, page: str | None = None,
               save_to: str | None = None) -> Image:
    """Picture of the Designer window (brought to the front). The image is sent to the model.
    view='schematic' (name = page title) or view='uci' (name = UCI, optional page) first opens that
    page fitted to the window; this clicks Designer's Design Elements panel, which has no UI Automation.
    save_to also writes the PNG to that path."""
    if view:
        designer.show_view(view, name or "", page)
    data = designer.screenshot()
    if save_to:
        with open(save_to, "wb") as fh:
            fh.write(data)
    return Image(data=data, format="png")


@tool(APP_SAFE)
def designer_check(path: str | None = None, discard_changes: bool = False) -> dict:
    """Run Designer's own File > Check Design on the open design (or open `path` first) and report
    errors, warnings and the licences the design needs; passed is true when no error was reported.
    Complements check_design, which checks the saved file statically."""
    if path:
        designer.open_design(path, discard_changes)
    return designer.check_design()


# ---- Lua -----------------------------------------------------------------------------------

@tool(READ_ONLY)
def lua_check(code: str) -> dict:
    """Syntax-check Lua (Q-SYS uses Lua 5.3) and flag common Q-SYS scripting mistakes."""
    return lua.check_syntax(code)


@tool(ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False))
def new_plugin(name: str, category: str = "Custom", author: str = "", description: str = "") -> str:
    """Scaffold a .qplug (TCP device driver template) in Documents/QSC/Q-Sys Designer/Plugins.
    Never overwrites an existing plugin."""
    return str(lua.scaffold_plugin(name, category, author, description))


# ---- QRC (runtime) -------------------------------------------------------------------------

@tool(LIVE_READ)
async def qrc_status() -> Any:
    """Engine status of the emulating or running design (design name, state, platform)."""
    return await qrc.status()


@tool(LIVE_READ)
async def qrc_list_components() -> Any:
    """Components visible over QRC (Script Access External or All)."""
    return await qrc.components()


@tool(LIVE_READ)
async def qrc_get_controls(component: str, controls: list[str] | None = None) -> Any:
    """All controls of a component, or only the named ones, with current values."""
    if controls:
        return await qrc.get(component, controls)
    return await qrc.controls(component)


@tool(LIVE_WRITE)
async def qrc_set_control(component: str, control: str, value: Any, ramp: float | None = None) -> Any:
    """Set a control on the running design (number, string or bool; optional ramp in seconds).
    On a real Core this changes a live system."""
    return await qrc.set(component, control, value, ramp)


@tool(LIVE_READ)
async def qrc_named_controls(names: list[str]) -> Any:
    """Get Named Controls by name."""
    return await qrc.named_get(names)


@tool(LIVE_WRITE)
async def qrc_set_named_control(name: str, value: Any, ramp: float | None = None) -> Any:
    """Set a Named Control on the running design. On a real Core this changes a live system."""
    return await qrc.named_set(name, value, ramp)


@tool(LIVE_READ)
async def qrc_watch(component: str, controls: list[str], seconds: float = 3.0, interval: float = 0.25) -> Any:
    """Sample controls over time, e.g. to confirm a script ramps a gain."""
    return await qrc.watch(component, controls, min(seconds, 30), max(interval, 0.05))


# ---- Knowledge ------------------------------------------------------------------------------

def _doc(name: str) -> str:
    return resources.files("qsys_mcp.knowledge").joinpath(name).read_text(encoding="utf-8")


@mcp.resource("qsys://spec-format")
def spec_format() -> str:
    """The JSON design spec that build_design takes."""
    return _doc("spec_format.md")


@mcp.resource("qsys://lua-api")
def lua_api() -> str:
    """Q-SYS Lua API cheat sheet."""
    return _doc("lua_api.md")


@mcp.resource("qsys://design-guide")
def design_guide() -> str:
    """Q-SYS design conventions and review checklist."""
    return _doc("design_guide.md")


@mcp.prompt()
def review_design(path: str) -> str:
    """Review a saved design against the checklist."""
    return (
        f"Review the Q-SYS design at {path}.\n"
        "1. read_design (use sections/components filters if it's large) and check_design with sensible path rules.\n"
        "2. If useful, emulate_design and inspect it with the qrc_* tools.\n"
        "3. Evaluate it against this checklist and give concrete, prioritised findings, each naming the component:\n\n"
        + _doc("design_guide.md")
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
