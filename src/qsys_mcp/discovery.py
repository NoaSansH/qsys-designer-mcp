"""Q-SYS Cores on the network, from the announcements they multicast (Q-SYS Discovery Protocol,
224.0.23.175 port 2467): name, model, addresses, whether it's the primary. Listening only: nothing is
sent. Windows may ask whether Python may receive on the network the first time.
"""

from __future__ import annotations

import re
import socket
import struct
import time

GROUP, PORT = "224.0.23.175", 2467
FIELDS = ("name", "type", "part_number", "lan_a_ip", "lan_b_ip", "aux_a_ip", "aux_b_ip", "is_primary", "is_virtual",
          "design_name", "design_code", "platform", "version")


def parse(packet: bytes) -> dict | None:
    """One announcement -> {"name", "model", "ips", ...}; None if it isn't a device announcement."""
    text = packet.decode("utf-8", "replace")
    if "<device>" not in text and "<device " not in text:
        return None
    found = {}
    for f in FIELDS:
        m = re.search(rf"<{f}>(.*?)</{f}>", text, re.S)
        if m and m.group(1).strip():
            found[f] = m.group(1).strip()
    if "name" not in found:
        return None
    out = {"name": found["name"], "model": found.get("part_number") or found.get("type", ""),
           "ips": [found[k] for k in ("lan_a_ip", "lan_b_ip", "aux_a_ip", "aux_b_ip") if k in found]}
    for k in ("type", "is_primary", "is_virtual", "design_name", "design_code", "platform", "version"):
        if k in found:
            out[k] = found[k]
    return out


def listen(seconds: float = 5.0) -> list[dict]:
    """Everything that announced itself during `seconds`, once per name."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", PORT))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, struct.pack("4sl", socket.inet_aton(GROUP), socket.INADDR_ANY))
        s.settimeout(0.5)
        seen: dict[str, dict] = {}
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                data, addr = s.recvfrom(65535)
            except socket.timeout:
                continue
            d = parse(data)
            if d:
                d.setdefault("ips", [])
                if addr[0] not in d["ips"]:
                    d["ips"].append(addr[0])
                seen[d["name"]] = d
        return sorted(seen.values(), key=lambda d: d["name"])
    finally:
        s.close()


def cores(devices: list[dict]) -> list[dict]:
    """The Cores among announced devices (other Q-SYS devices announce too)."""
    return [d for d in devices if re.search(r"core|lcqln|qsc_core", f"{d.get('model', '')} {d.get('type', '')}", re.I)]
