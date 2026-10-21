"""Snapshot contents read straight from a design file, without Designer or its emulator.

The engine saves each snapshot of a bank as a file inside the design ("controlset.<bank file>.snapshot.N").
Such a file is a 'q' byte, an LZ-compressed record list and a 32-character check (an MD5 the engine salts,
so files can be read here but are still written by the engine: record_snapshots).

Compression: a control byte c; c < 0x80 is followed by c literal bytes, otherwise it copies c & 0x1f bytes
from ((c >> 5) & 3) << 8 | next byte back in the output.
Records: 'i' int32, 'e' float32, 's' int32 length + bytes. The list is i FORMAT, i count, then per control
s "<block id><_control>", i kind (7 number, 4 text) and the value (e or s). The block id is the Ascii85
of the block's GUID (bytes in .NET order).
"""

from __future__ import annotations

import base64
import os
import struct
import uuid

from qsys_mcp import headless

FORMAT = 200801241
KINDS = {7: "number", 4: "text"}


class SnapshotFormatError(ValueError):
    pass


def unpack(raw: bytes) -> bytes:
    if len(raw) < 33 or raw[0] != 0x71:
        raise SnapshotFormatError("not a snapshot file")
    out, i, end = bytearray(), 1, len(raw) - 32
    while i < end:
        c = raw[i]
        i += 1
        if c & 0x80:
            n, back = c & 0x1F, ((c >> 5) & 3) << 8 | raw[i]
            i += 1
            if back == 0 or back > len(out):
                raise SnapshotFormatError("bad back reference")
            for _ in range(n):
                out.append(out[-back])
        else:
            out += raw[i:i + c]
            i += c
    return bytes(out)


def records(data: bytes) -> list:
    out, i = [], 0
    while i < len(data):
        tag = data[i:i + 1]
        i += 1
        if tag == b"i":
            out.append(struct.unpack_from("<i", data, i)[0])
            i += 4
        elif tag == b"e":
            out.append(struct.unpack_from("<f", data, i)[0])
            i += 4
        elif tag == b"s":
            n = struct.unpack_from("<i", data, i)[0]
            out.append(data[i + 4:i + 4 + n].decode("utf-8", "replace"))
            i += 4 + n
        else:
            raise SnapshotFormatError(f"unknown record {tag!r} at {i - 1}")
    return out


def parse(raw: bytes) -> list[tuple[str, str, object]]:
    """(block id, control, value) for each control a snapshot file holds."""
    r = records(unpack(raw))
    if len(r) < 2 or r[0] != FORMAT:
        raise SnapshotFormatError(f"snapshot format {r[0] if r else None} is not {FORMAT}")
    out, i = [], 2
    for _ in range(r[1]):
        key, kind, value = r[i], r[i + 1], r[i + 2]
        i += 3
        if isinstance(value, float):
            value = int(value) if value.is_integer() else float(f"{value:.7g}")
        out.append((key[:20], key[21:], value))
    return out


def engine_id(guid: str) -> str:
    return base64.a85encode(uuid.UUID(guid).bytes_le).decode().replace("z", "!!!!!")


def contents(path: str, design: dict | None = None) -> dict:
    """What each saved snapshot sets: {bank: {"1": {"BLOCK.control": value}}}, numbers as numbers and
    text as text. A control of a block no longer in the design shows as "?<id>.control"."""
    from qsys_mcp import designs
    path = os.path.abspath(path)
    design = design or designs.read(path)
    names = {engine_id(c["uid"]): c["name"] for c in design["components"] if c.get("uid")}
    files = headless.run({"command": "core_files", "path": path, "prefix": "controlset."}, headless.pick(for_file=path))
    by_file = {v: k for k, v in files["banks"].items()}
    out: dict = {}
    for f in files["files"]:
        stem, _, number = f["name"][len("controlset."):].rpartition(".snapshot.")
        bank = by_file.get(stem)
        if bank is None:
            continue
        values = {}
        for block, control, value in parse(base64.b64decode(f["base64"])):
            values[f"{names.get(block, '?' + block)}.{control}"] = value
        out.setdefault(bank, {})[number] = dict(sorted(values.items()))
    return {b: dict(sorted(s.items(), key=lambda kv: int(kv[0]))) for b, s in sorted(out.items())}
