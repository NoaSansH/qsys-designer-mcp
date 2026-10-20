"""Headless access to Q-SYS Designer's own libraries.

The C# helper in `helper/` is compiled on first use against the Designer version being used
(with the .NET Framework compiler that ships with Windows) and cached per version. Each call
runs it once with a JSON request and reads back a JSON response. Nothing from QSC is shipped.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / r"Microsoft.NET\Framework64\v4.0.30319\csc.exe"
HELPER_SOURCES = ("QCommon.cs", "QsysHelper.cs", "QsysHelper.Signals.cs", "QsysHelper.Graphics.cs")
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


def _net8_helper(inst: Install) -> Path:
    """Designer 10+: the prebuilt helper (no QSC code), set to run on exactly Designer's own runtime."""
    pkg = Path(str(resources.files("qsys_mcp.helper"))) / "net8"
    if not (pkg / "qhelper.dll").is_file():
        raise HelperError(f"The prebuilt .NET helper is missing from {pkg}; build it with "
                          "'dotnet publish helper-net8 -c Release -o src/qsys_mcp/helper/net8'")
    designer_cfg = json.loads((inst.folder / "Q-Sys Designer.runtimeconfig.json").read_text(encoding="utf-8-sig"))
    opts = designer_cfg.get("runtimeOptions", {})
    frameworks = opts.get("frameworks") or [opts["framework"]]
    digest = hashlib.sha256((inst.version + (pkg / "qhelper.dll").read_bytes().hex()[:4096] + json.dumps(frameworks)).encode()).hexdigest()[:12]
    out_dir = cache_dir() / "helper" / f"{inst.version}-net-{digest}"
    exe = out_dir / "qhelper.exe"
    if exe.is_file():
        return exe
    out_dir.mkdir(parents=True, exist_ok=True)
    for f in pkg.iterdir():
        if f.suffix in (".dll", ".exe", ".json"):
            (out_dir / f.name).write_bytes(f.read_bytes())
    cfg = {"runtimeOptions": {"tfm": opts.get("tfm", "net8.0"), "frameworks": frameworks,
                              "configProperties": {"System.Runtime.Serialization.EnableUnsafeBinaryFormatterSerialization": True}}}
    (out_dir / "qhelper.runtimeconfig.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return exe


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


def run(request: dict, inst: Install | None = None, timeout: float = 300) -> dict:
    """Run one helper command and return its response (raises HelperError on failure)."""
    inst = inst or pick()
    exe = helper_exe(inst)
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
        out = json.loads(resp.read_text(encoding="utf-8-sig"))
    if not out.get("ok"):
        raise HelperError(out.get("error", "unknown helper error"))
    out.pop("ok", None)
    out["designer"] = inst.version
    return out


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
