"""Running designs in the Q-SYS Designer app: open, emulate, stop, answer dialogs, screenshot.

Nothing here edits a design (that's all headless). Menus and dialog buttons are driven through
UI Automation patterns (expand / invoke), so no mouse or keyboard input is injected.
Designer opens each file in its own process, so this keeps one Designer running at a time.
"""

from __future__ import annotations

import ctypes
import io
import os
import re
import subprocess
import time

from pywinauto import Desktop

from qsys_mcp import headless

TITLE_RE = re.compile(r"^(?P<design>.*?) - Q-SYS Designer \[(?P<version>[^\]]+)\](?P<rest>.*)$")
MAX_SHOT_WIDTH = 1600


class AppError(RuntimeError):
    pass


# ---- windows / processes -------------------------------------------------------------

def _designer_windows() -> list:
    """Top-level Designer main windows (one per running Designer process)."""
    return [w for w in Desktop(backend="uia").windows() if TITLE_RE.match(w.window_text() or "")]


def _title_unsaved(title: str) -> bool:
    m = TITLE_RE.match(title)
    return bool(m) and m["design"].endswith("*")


def _process_alive(pid: int) -> bool:
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
    if not h:
        return False
    try:
        return k32.WaitForSingleObject(h, 0) == 0x102  # WAIT_TIMEOUT: still running
    finally:
        k32.CloseHandle(h)


def _norm(name: str) -> str:
    """'&No', '_Don't Save', 'Don’t save' -> comparable lower-case text."""
    return name.replace("&", "").replace("_", "").replace("’", "'").strip().lower()


def _invoke(el) -> None:
    """Press a button / menu item through UI Automation; fall back to a click only if the
    element (or its parent, for task-dialog command links) has no Invoke pattern."""
    for target in (el, el.parent()):
        try:
            target.iface_invoke.Invoke()
            return
        except Exception:
            continue
    el.click_input()


class Designer:
    def __init__(self) -> None:
        self._win = None

    # ---- state ---------------------------------------------------------------------
    def instances(self) -> list[dict]:
        out = []
        for w in _designer_windows():
            m = TITLE_RE.match(w.window_text())
            design = m["design"] if m else w.window_text()
            out.append({"pid": w.process_id(), "title": w.window_text(), "design": design.rstrip("*"),
                        "unsaved_changes": design.endswith("*")})
        return out

    def window(self):
        w = self._win
        if w is not None:
            try:
                if ctypes.windll.user32.IsWindow(w.handle) and TITLE_RE.match(w.window_text() or ""):
                    return w
            except Exception:
                pass
        wins = _designer_windows()
        if not wins:
            raise AppError("Q-SYS Designer is not running. Use open_design to open a file.")
        self._win = wins[0]
        return self._win

    def status(self) -> dict:
        try:
            win = self.window()
        except AppError as e:
            return {"running": False, "detail": str(e)}
        title = win.window_text()
        m = TITLE_RE.match(title)
        info = {"running": True, "title": title, "design": m["design"].rstrip("*"), "version": m["version"],
                "unsaved_changes": m["design"].endswith("*"), "mode": self._mode(win)}
        others = [i["title"] for i in self.instances() if i["pid"] != win.process_id()]
        if others:
            info["other_designer_instances"] = others
        return info

    @staticmethod
    def _mode(win) -> str:
        texts = [t.window_text() for t in win.descendants(control_type="Text") if t.window_text().startswith("System is")]
        return texts[0] if texts else "design (not emulating or connected)"

    # ---- open / close ----------------------------------------------------------------
    def close_designs(self, discard_changes: bool = False) -> list[str]:
        """Close every running Designer. Refuses, closing nothing, if any has unsaved changes,
        unless discard_changes. Stops emulation first. Returns the windows closed."""
        wins = _designer_windows()
        dirty = [w.window_text() for w in wins if _title_unsaved(w.window_text())]
        if dirty and not discard_changes:
            raise AppError("Not closing Designer: unsaved changes in " + "; ".join(dirty) +
                           ". Save them in Designer first, or pass discard_changes=True if they don't matter.")
        closed = [self._close_instance(w, discard_changes) for w in wins]
        self._win = None
        return closed

    def _close_instance(self, w, discard_changes: bool) -> str:
        title, pid = w.window_text(), w.process_id()
        self._win = w
        try:
            if self._mode(w) == "System is emulating":
                self.menu("File->Disconnect")
                time.sleep(2)
        except Exception:
            pass
        w.close()
        deadline = time.time() + 30
        while time.time() < deadline:
            if not _process_alive(pid):
                return title
            # once its main window is gone the process is just shutting down
            if not ctypes.windll.user32.IsWindow(w.handle):
                time.sleep(0.5)
                continue
            self._win = w
            try:
                open_dialogs = self.dialogs()
            except Exception:   # the window went away mid-scan: it's closing
                time.sleep(0.5)
                continue
            for dlg in open_dialogs:
                if self.is_license_notice(dlg):
                    self.press("OK")
                    w.close()       # the close request was swallowed while the notice was up
                    continue
                choices = {_norm(b): b for b in dlg["buttons"]}
                no = choices.get("don't save") or choices.get("no")
                if no and discard_changes:
                    self.press(no)
                elif no and "cancel" in choices:
                    self.press(choices["cancel"])
                    raise AppError(f"Designer asked to save {title!r}; cancelled the close. Dialog: {dlg}")
                else:
                    raise AppError(f"Unexpected dialog while closing {title!r} (left open): {dlg}")
            time.sleep(0.5)
        raise AppError(f"Designer ({title!r}, pid {pid}) did not close within 30 s")

    def open_design(self, path: str, discard_changes: bool = False, timeout: float = 180) -> dict:
        """Close whatever Designer has open, then start the matching Designer version on `path`."""
        p = os.path.abspath(path)
        if not os.path.isfile(p) or not p.lower().endswith(".qsys"):
            raise AppError(f"No .qsys file at {p}")
        inst = headless.pick(for_file=p)
        closed = self.close_designs(discard_changes)
        subprocess.Popen([str(inst.exe), p], close_fds=True)
        want = os.path.splitext(os.path.basename(p))[0]
        deadline = time.time() + timeout
        while time.time() < deadline:
            for w in _designer_windows():
                m = TITLE_RE.match(w.window_text())
                if m and m["design"].rstrip("*") == want:
                    self._win = w
                    time.sleep(2)  # let the schematic finish loading
                    return {"opened": p, "designer": inst.version, "closed": closed, "dialogs": self.dialogs(),
                            "title": w.window_text()}
            time.sleep(1)
        raise AppError(f"Designer did not show {want!r} within {timeout:.0f} s")

    # ---- menus and dialogs -------------------------------------------------------------
    def _popups(self) -> list:
        main = self.window()
        return [main] + [w for w in Desktop(backend="uia").windows(process=main.process_id()) if w.handle != main.handle]

    def menu(self, path: str) -> None:
        """Run a main-menu command such as 'File->Emulate' (expand/invoke, no clicks)."""
        parts = [p.strip() for p in path.split("->")]
        win = self.window()
        bar = next((e for e in win.descendants(control_type="Menu") if e.element_info.automation_id == "mainMenu"), win)
        item = None
        for i, part in enumerate(parts):
            scopes = [bar] if i == 0 else self._popups()
            item = next((mi for s in scopes for mi in s.descendants(control_type="MenuItem")
                         if _norm(mi.window_text()) == _norm(part)), None)
            if item is None:
                raise AppError(f"Menu item {part!r} not found in {path!r}")
            if i < len(parts) - 1:
                item.iface_expand_collapse.Expand()
                time.sleep(0.4)
        _invoke(item)
        time.sleep(0.5)

    def _modal_windows(self):
        main = self.window()
        for w in self._popups():
            scope = [d for d in w.children() if d.element_info.control_type == "Window"] if w.handle == main.handle else [w]
            for dlg in scope:
                try:
                    if dlg.iface_window.CurrentIsModal:
                        yield dlg
                except Exception:
                    pass

    @staticmethod
    def _choices(dlg) -> list[tuple[str, object]]:
        # task dialogs (e.g. the save prompt) show their choices as command links: Text elements
        # labelled with an access-key underscore ("_Don't Save"), not named Buttons
        out = [(b.window_text().strip(), b) for b in dlg.descendants(control_type="Button") if b.window_text().strip()]
        out += [(t.window_text()[1:].strip(), t) for t in dlg.descendants(control_type="Text")
                if t.window_text().startswith("_") and len(t.window_text()) > 1]
        return out

    def dialogs(self) -> list[dict]:
        out = []
        for dlg in self._modal_windows():
            texts = [t.window_text() for t in dlg.descendants(control_type="Text")
                     if t.window_text() and not t.window_text().startswith("_")]
            out.append({"title": dlg.window_text(), "text": texts, "buttons": [n for n, _ in self._choices(dlg)]})
        return out

    @staticmethod
    def is_license_notice(dlg: dict) -> bool:
        """'Design License Requirements Have Changed': an OK-only notice that's always safe to acknowledge."""
        return dlg["buttons"] == ["OK"] and any("License Requirements" in t for t in dlg["text"])

    def ack_license_notices(self) -> list[str]:
        notices = []
        for dlg in self.dialogs():
            if self.is_license_notice(dlg):
                notices.append(" ".join(t.strip() for t in dlg["text"] if t.strip() and t.strip() != "OK"))
                self.press("OK")
        return notices

    def press(self, button: str) -> None:
        want = _norm(button)
        for dlg in self._modal_windows():
            for name, el in self._choices(dlg):
                if _norm(name) == want:
                    _invoke(el)
                    time.sleep(0.5)
                    return
        raise AppError(f"No open dialog has a {button!r} button; dialogs: {self.dialogs()}")

    # ---- emulation ----------------------------------------------------------------------
    def emulate(self) -> dict:
        self.menu("File->Emulate")
        time.sleep(2)
        return {"mode": self._mode(self.window()), "dialogs": self.dialogs()}

    def stop(self) -> dict:
        self.menu("File->Disconnect")
        time.sleep(1.5)
        return {"mode": self._mode(self.window()), "dialogs": self.dialogs()}

    # ---- Check Design ---------------------------------------------------------------------
    def check_design(self) -> dict:
        """File > Check Design... and what its dialogs say. Problems are the lines that mention an
        error or a warning; the resources report (DSP, network streams, licences) comes back as is."""
        for attempt in range(5):   # right after opening, the File menu isn't populated yet
            try:
                self.menu("File->Check Design...")
                break
            except AppError:
                if attempt == 4:
                    raise
                time.sleep(2)
        dialogs, deadline = [], time.time() + 60
        while time.time() < deadline:
            time.sleep(1)
            dialogs = self.dialogs()
            if dialogs and not any(d["title"] == "Core Status" for d in dialogs):
                break
        out = {"dialogs": dialogs, "errors": [], "warnings": [], "licenses": []}
        for d in dialogs:
            texts = [t.strip() for t in d["text"] if t.strip()]
            for t in texts:
                low = t.lower()
                if "error" in low and "0 error" not in low:
                    out["errors"].append(t)
                elif "warning" in low and "0 warning" not in low:
                    out["warnings"].append(t)
            if "Q-SYS Software License Type" in texts:
                i = texts.index("Quantity or Type")
                out["licenses"] = [t for t in texts[i + 1:] if not t.startswith("Q-SYS Reflect")][:12]
        for d in dialogs:
            for b in ("OK", "Close"):
                if b in d["buttons"]:
                    self.press(b)
                    break
        out["passed"] = not out["errors"]
        return out

    # ---- views -----------------------------------------------------------------------------
    def _ensure_front(self) -> None:
        """Clicks and screenshots act on whatever is on screen: refuse unless Designer is the window
        in front, so nothing lands in (or captures) another application the user is working in."""
        win = self.window()
        if win.is_minimized():
            win.restore()
        try:
            win.set_focus()
        except Exception:
            pass
        time.sleep(0.3)
        front = ctypes.windll.user32.GetForegroundWindow()
        if front != win.handle:
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetWindowTextW(front, buf, 256)
            raise AppError(f"Designer is not the window in front ({buf.value or 'another window'} is); "
                           "nothing was clicked or captured. Bring Designer to the front, then try again.")

    def _click_text(self, text: str, double: bool = False, within=None) -> None:
        """Designer's Design Elements panels have no UI Automation patterns, so these few views are
        opened with a mouse click on their label (the only place this module clicks), and only while
        Designer is the window in front."""
        scope = within or self.window()
        hits = [e for e in scope.descendants() if e.window_text() == text and e.is_visible()]
        if not hits:
            raise AppError(f"Nothing labelled {text!r} is visible in Designer")
        self._ensure_front()
        el = hits[-1]
        el.double_click_input() if double else el.click_input()
        time.sleep(1.2)

    def _open_from_panel(self, header: str, item: str) -> None:
        """Expand a Design Elements section, then open one of its items. A click on the header can be
        lost while Designer redraws, so it's retried until the item shows."""
        win = self.window()

        def tab_open() -> bool:
            # the opened page gets a tab (a UCI's reads "UCI name: page"); the tabs have no selection pattern
            return any((t.window_text() or "").startswith(item) for t in win.descendants(control_type="TabItem"))

        for _ in range(3):
            self._click_text(header)
            deadline = time.time() + 5
            while time.time() < deadline:
                if any(e.window_text() == item and e.is_visible() for e in win.descendants(control_type="Text")):
                    self._click_text(item, double=True)
                    opened = time.time() + 10   # a UCI's editor takes a few seconds to open
                    while time.time() < opened:
                        if tab_open():
                            time.sleep(1)
                            return
                        time.sleep(0.5)
                    break
                time.sleep(0.5)
        raise AppError(f"Could not open {item!r} from {header!r}")

    def show_view(self, kind: str, name: str, page: str | None = None, fit: bool = True) -> dict:
        """Bring a schematic page (kind 'schematic', name = page title) or a UCI (kind 'uci', name =
        UCI name, optional page) to the front, fitted to the window."""
        win = self.window()
        win.set_focus()
        if kind == "schematic":
            self._open_from_panel("Schematic Pages", name)
        elif kind == "uci":
            self._open_from_panel("User Control Interfaces", name)
            if page:
                self._click_text(page)
        else:
            raise AppError("kind must be 'schematic' or 'uci'")
        if fit:
            try:
                self.menu("View->Fit To Screen")
            except AppError:
                pass
            time.sleep(1)
        return {"shown": kind, "name": name, "page": page, "title": self.window().window_text()}

    # ---- screenshot ------------------------------------------------------------------------
    def screenshot(self) -> bytes:
        """PNG of the Designer window (brought to the front first; refused if it can't be)."""
        import mss
        from PIL import Image
        win = self.window()
        self._ensure_front()   # the grab is of the screen: never capture another application
        r = win.rectangle()
        with mss.mss() as sct:
            raw = sct.grab({"left": r.left, "top": r.top, "width": r.width(), "height": r.height()})
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        if img.width > MAX_SHOT_WIDTH:
            img = img.resize((MAX_SHOT_WIDTH, round(img.height * MAX_SHOT_WIDTH / img.width)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
