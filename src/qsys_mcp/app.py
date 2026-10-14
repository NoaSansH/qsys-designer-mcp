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

    # ---- screenshot ------------------------------------------------------------------------
    def screenshot(self) -> bytes:
        """PNG of the Designer window (brought to the front first)."""
        import mss
        from PIL import Image
        win = self.window()
        if win.is_minimized():
            win.restore()
        win.set_focus()
        time.sleep(0.3)
        r = win.rectangle()
        with mss.mss() as sct:
            raw = sct.grab({"left": r.left, "top": r.top, "width": r.width(), "height": r.height()})
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        if img.width > MAX_SHOT_WIDTH:
            img = img.resize((MAX_SHOT_WIDTH, round(img.height * MAX_SHOT_WIDTH / img.width)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
