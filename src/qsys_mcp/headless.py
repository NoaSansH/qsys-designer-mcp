"""Headless access to Q-SYS Designer's own libraries.

The C# helper in `helper/` is compiled on first use against the Designer version being used
(with the .NET Framework compiler that ships with Windows) and cached per version. A JSON request
goes in and a JSON response comes back. One helper process per Designer version stays open and takes
the requests in turn, so Designer's libraries load once (QSYS_MCP_PERSISTENT=0 runs one process per
request instead). Nothing from QSC is shipped.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import atexit
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / r"Microsoft.NET\Framework64\v4.0.30319\csc.exe"
HELPER_SOURCES = ("QCommon.cs", "QUci.cs", "QsysHelper.cs", "QsysHelper.Edit.cs")
QSC_ROOTS = [Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "QSC",
             Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "QSC"]


class HelperError(RuntimeError):
    pass


@dataclass(frozen=True)
class Install:
    folder: Path
    version: str  # file version of Q-Sys Designer.exe, e.g. "9.13.1.0"

    @property
    def exe(self) -> Path:
        return self.folder / "Q-Sys Designer.exe"

    @property
    def short(self) -> str:
        return ".".join(self.version.split(".")[:2])


def cache_dir() -> Path:
    base = os.environ.get("QSYS_MCP_CACHE") or os.path.join(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()), "qsys-designer-mcp")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file_version(path: Path) -> str | None:
    ver = ctypes.windll.version
    size = ver.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    buf = ctypes.create_string_buffer(size)
    if not ver.GetFileVersionInfoW(str(path), 0, size, buf):
        return None
    ptr, length = ctypes.c_void_p(), ctypes.c_uint()
    if not ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(length)):
        return None
    fixed = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint32 * 13)).contents
    ms, ls = fixed[2], fixed[3]
    return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"


def _vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v))


def installs() -> list[Install]:
    """Installed Designer versions, newest first. QSYS_DESIGNER_DIR adds (or pins) a folder."""
    folders = []
    if os.environ.get("QSYS_DESIGNER_DIR"):
        folders.append(Path(os.environ["QSYS_DESIGNER_DIR"]))
    for root in QSC_ROOTS:
        if root.is_dir():
            folders += sorted(p for p in root.iterdir() if p.name.startswith("Q-SYS Designer"))
    found, seen = [], set()
    for f in folders:
        exe = f / "Q-Sys Designer.exe"
        if exe.is_file() and f not in seen:
            seen.add(f)
            found.append(Install(f, _file_version(exe) or re.sub(r"[^\d.]", "", f.name)))
    return sorted(found, key=lambda i: _vkey(i.version), reverse=True)


def file_designer_version(path: str | Path) -> str | None:
    """The Designer version that saved a .qsys file (from its serialisation header)."""
    with open(path, "rb") as fh:
        head = fh.read(4096)
    m = re.search(rb"Q-Sys Designer, Version=([\d.]+)", head)
    return m.group(1).decode() if m else None


def pick(version: str | None = None, for_file: str | Path | None = None) -> Install:
    """Choose an install: an explicit version prefix ('10.3'), else QSYS_DESIGNER_DIR, else the one
    matching the file's saving version (major.minor), else the newest."""
    found = installs()
    if not found:
        raise HelperError("No Q-SYS Designer installation found. Install Designer, or set QSYS_DESIGNER_DIR "
                          "to its folder (the one containing 'Q-Sys Designer.exe').")
    if version:
        for i in found:
            if i.version.startswith(version):
                return i
        raise HelperError(f"Designer {version} is not installed; installed: {[i.version for i in found]}")
    if os.environ.get("QSYS_DESIGNER_DIR"):
        return found[[str(i.folder) for i in found].index(str(Path(os.environ["QSYS_DESIGNER_DIR"])))]
    if for_file:
        fv = file_designer_version(for_file)
        if fv:
            for i in found:
                if i.short == ".".join(fv.split(".")[:2]):
                    return i
    return found[0]


def _sources() -> dict[str, str]:
    pkg = resources.files("qsys_mcp.helper")
    return {name: (pkg / name).read_text(encoding="utf-8") for name in HELPER_SOURCES}


def is_dotnet_core(inst: Install) -> bool:
    """Designer 10 and later run on modern .NET (code in 'Q-Sys Designer.dll'); 9.x on .NET Framework."""
    return (inst.folder / "Q-Sys Designer.dll").is_file() and (inst.folder / "Q-Sys Designer.runtimeconfig.json").is_file()


NET_SETUP = "2"   # 2: Designer's app-local packages (BinaryFormatter on .NET 9+)


def _net8_helper(inst: Install) -> Path:
    """Designer 10+: the prebuilt helper (no QSC code), set to run on exactly Designer's own runtime."""
    pkg = Path(str(resources.files("qsys_mcp.helper"))) / "net8"
    if not (pkg / "qhelper.dll").is_file():
        raise HelperError(f"The prebuilt .NET helper is missing from {pkg}; build it with "
                          "'dotnet publish helper-net8 -c Release -o src/qsys_mcp/helper/net8'")
    designer_cfg = json.loads((inst.folder / "Q-Sys Designer.runtimeconfig.json").read_text(encoding="utf-8-sig"))
    opts = designer_cfg.get("runtimeOptions", {})
    frameworks = opts.get("frameworks") or [opts["framework"]]
    # the whole helper is hashed: a rebuilt one differs well after its (identical) PE header
    # NET_SETUP: bump when the way the helper is set up changes, so cached copies are redone
    h = hashlib.sha256((NET_SETUP + inst.version + json.dumps(frameworks)).encode())
    for f in sorted(pkg.iterdir()):
        if f.suffix in (".dll", ".exe", ".json"):
            h.update(f.read_bytes())
    out_dir = cache_dir() / "helper" / f"{inst.version}-net-{h.hexdigest()[:12]}"
    exe = out_dir / "qhelper.exe"
    if exe.is_file():
        return exe
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in pkg.iterdir():
        if f.suffix in (".dll", ".exe", ".json"):
            (out_dir / f.name).write_bytes(f.read_bytes())
    _bring_designer_packages(inst, out_dir)
    cfg = {"runtimeOptions": {"tfm": opts.get("tfm", "net8.0"), "frameworks": frameworks,
                              "configProperties": {"System.Runtime.Serialization.EnableUnsafeBinaryFormatterSerialization": True}}}
    (out_dir / "qhelper.runtimeconfig.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return exe


# Packages Designer ships next to itself that must win over the runtime's own copy. On .NET 9 and
# later, BinaryFormatter (which .qsys files use) only works from the System.Runtime.Serialization.Formatters
# package; the framework's assembly of that name is a stub that throws.
APP_LOCAL_PACKAGES = ("System.Runtime.Serialization.Formatters",)


def _bring_designer_packages(inst: Install, out_dir: Path) -> None:
    deps_file = inst.folder / "Q-Sys Designer.deps.json"
    if not deps_file.is_file():
        return
    designer = json.loads(deps_file.read_text(encoding="utf-8-sig"))
    target = {}   # packages may sit under the plain target or the runtime one (".../win-x64")
    for libs in designer.get("targets", {}).values():
        target.update({k: v for k, v in libs.items() if v.get("runtime")})
    mine_file = out_dir / "qhelper.deps.json"
    mine = json.loads(mine_file.read_text(encoding="utf-8-sig"))
    my_target = next(iter(mine["targets"].values()))
    changed = False
    for key, lib in target.items():
        name = key.split("/")[0]
        if name not in APP_LOCAL_PACKAGES:
            continue
        for asset_path, info in (lib.get("runtime") or {}).items():
            dll = inst.folder / Path(asset_path).name
            if not dll.is_file():
                continue
            (out_dir / dll.name).write_bytes(dll.read_bytes())
            # declared as a project-local file next to the helper, with Designer's versions, so the host
            # prefers it to the framework's lower file version
            my_target[key] = {"runtime": {dll.name: info}}
            mine["libraries"][key] = {"type": "project", "serviceable": False, "sha512": ""}
            my_target["qhelper/1.0.0"].setdefault("dependencies", {})[name] = key.split("/")[1]
            changed = True
    if changed:
        mine_file.write_text(json.dumps(mine, indent=2), encoding="utf-8")


def helper_exe(inst: Install) -> Path:
    """The helper for this Designer: prebuilt for 10+ (.NET), compiled once per version for 9.x."""
    if is_dotnet_core(inst):
        return _net8_helper(inst)
    src = _sources()
    digest = hashlib.sha256((inst.version + "".join(src[n] for n in HELPER_SOURCES)).encode()).hexdigest()[:12]
    out_dir = cache_dir() / "helper" / f"{inst.version}-{digest}"
    exe = out_dir / "qhelper.exe"
    if exe.is_file():
        return exe
    if not CSC.is_file():
        raise HelperError(f"The .NET Framework 4 C# compiler was not found at {CSC}")
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in src.items():
        (out_dir / name).write_text(text, encoding="utf-8")
    wpf = CSC.parent / "WPF"
    cmd = [str(CSC), "/nologo", "/platform:x64", "/target:exe", "/optimize", f"/out:{exe}",
           f"/r:{inst.exe}", f"/r:{inst.folder / 'Newtonsoft.Json.dll'}",
           f"/r:{wpf / 'WindowsBase.dll'}", f"/r:{wpf / 'PresentationCore.dll'}", f"/r:{wpf / 'PresentationFramework.dll'}",
           "/r:System.Xaml.dll", "/r:System.Core.dll", "/r:System.dll"] + [str(out_dir / n) for n in HELPER_SOURCES]
    r = subprocess.run(cmd, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0 or not exe.is_file():
        raise HelperError(f"Compiling the helper against Designer {inst.version} failed:\n{r.stdout}\n{r.stderr}")
    return exe


DONE_MARK = "##qhelper-done"
RECYCLE_AFTER = 40          # requests before a serving helper is restarted (Designer keeps some state)
_workers: dict[str, "_Worker"] = {}
_workers_lock = threading.Lock()


def persistent() -> bool:
    return os.environ.get("QSYS_MCP_PERSISTENT", "1") != "0"


class _Worker:
    """A helper process serving requests for one Designer version, one at a time."""

    def __init__(self, inst: Install, exe: Path) -> None:
        self.exe, self.lock, self.served = exe, threading.Lock(), 0
        self.tmp = tempfile.mkdtemp(prefix="qsys-mcp-serve-")
        self.proc = subprocess.Popen([str(exe), str(inst.folder), "--serve"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                     errors="replace", cwd=self.tmp, creationflags=subprocess.CREATE_NO_WINDOW)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            self.lines.put(line.rstrip("\r\n"))
        self.lines.put(None)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(5)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def call(self, request: dict, timeout: float) -> tuple[Path | None, str]:
        """(response file, helper output); the file is None if the helper died or timed out."""
        n = self.served = self.served + 1
        req, resp = Path(self.tmp) / f"request{n}.json", Path(self.tmp) / f"response{n}.json"
        req.write_text(json.dumps(request), encoding="utf-8")
        self.proc.stdin.write(f"{req}\t{resp}\n")
        self.proc.stdin.flush()
        log, deadline = [], time.monotonic() + timeout
        while True:
            try:
                line = self.lines.get(timeout=max(0.1, deadline - time.monotonic()))
            except queue.Empty:
                self.proc.kill()
                raise HelperError(f"Helper timed out after {timeout:.0f} s ({request.get('command')})")
            if line is None:
                return None, "\n".join(log)
            if line == DONE_MARK:
                req.unlink(missing_ok=True)
                return resp, "\n".join(log)
            log.append(line)


def _serve(request: dict, inst: Install, exe: Path, timeout: float) -> dict | None:
    """The response from this version's serving helper, or None if it could not answer (it is then
    restarted next time and this request runs on its own)."""
    with _workers_lock:
        w = _workers.get(inst.version)
        if w is None or not w.alive() or w.exe != exe or w.served >= RECYCLE_AFTER:
            if w is not None:
                w.stop()
            try:
                w = _workers[inst.version] = _Worker(inst, exe)
            except OSError:
                return None
    with w.lock:
        if not w.alive():
            return None
        resp, log = w.call(request, timeout)
        if resp is None or not resp.is_file():
            return None
        out = json.loads(resp.read_text(encoding="utf-8-sig"))
        resp.unlink(missing_ok=True)
        return out


@atexit.register
def stop_workers() -> None:
    with _workers_lock:
        for w in _workers.values():
            w.stop()
        _workers.clear()


def run(request: dict, inst: Install | None = None, timeout: float = 300) -> dict:
    """Run one helper command and return its response (raises HelperError on failure)."""
    inst = inst or pick()
    exe = helper_exe(inst)
    out = _serve(request, inst, exe, timeout) if persistent() else None
    if out is None:
        out = _run_once(request, inst, exe, timeout)
    if not out.get("ok"):
        raise HelperError(out.get("error", "unknown helper error"))
    out.pop("ok", None)
    out["designer"] = inst.version
    return out


def _run_once(request: dict, inst: Install, exe: Path, timeout: float) -> dict:
    with tempfile.TemporaryDirectory(prefix="qsys-mcp-") as tmp:
        req, resp = Path(tmp) / "request.json", Path(tmp) / "response.json"
        req.write_text(json.dumps(request), encoding="utf-8")
        try:
            r = subprocess.run([str(exe), str(inst.folder), str(req), str(resp)], capture_output=True, text=True,
                               timeout=timeout, cwd=tmp, creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired as e:
            raise HelperError(f"Helper timed out after {timeout:.0f} s ({request.get('command')})") from e
        if not resp.is_file():
            raise HelperError(f"Helper crashed ({request.get('command')}): exit {r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}")
        return json.loads(resp.read_text(encoding="utf-8-sig"))


# ---- calibrated positions -------------------------------------------------------------

def _positions_file(inst: Install) -> Path:
    return cache_dir() / f"positions-{inst.version}.json"


def load_positions(inst: Install) -> list[dict]:
    p = _positions_file(inst)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else []


def save_positions(inst: Install, entries: list[dict]) -> None:
    known = {(e["class"], e["control"], e["min"], e["max"], round(e["value"], 6)): e for e in load_positions(inst)}
    for e in entries:
        known[(e["class"], e["control"], e["min"], e["max"], round(e["value"], 6))] = e
    _positions_file(inst).write_text(json.dumps(list(known.values()), indent=1), encoding="utf-8")
