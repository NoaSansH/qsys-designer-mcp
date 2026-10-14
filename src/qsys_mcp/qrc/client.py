"""Minimal Q-SYS Remote Control (QRC) client.

QRC is JSON-RPC 2.0 over TCP port 1710; each message is terminated by a NUL
byte. Designer exposes it on localhost while a design is emulating. Components
are only visible over QRC when their "Script Access" property allows it.
"""

from __future__ import annotations

import asyncio
import itertools
import json
from typing import Any


class QrcError(RuntimeError):
    pass


class QrcClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 1710, timeout: float = 5.0,
                 user: str | None = None, password: str | None = None):
        self.host, self.port, self.timeout = host, port, timeout
        self.user, self.password = user, password
        self._ids = itertools.count(1)

    async def call(self, method: str, params: Any = None) -> Any:
        """Open a connection, (optionally log on), make one call, close.

        A connection per call keeps things robust across emulation restarts.
        """
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), self.timeout)
        except (OSError, asyncio.TimeoutError) as e:
            raise QrcError(
                f"Cannot reach QRC at {self.host}:{self.port} ({e}). "
                "Is the design emulating or running?") from e
        try:
            if self.user:
                await self._send(reader, writer, "Logon", {"User": self.user, "Password": self.password or ""})
            return await self._send(reader, writer, method, params)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _send(self, reader, writer, method, params):
        msg_id = next(self._ids)
        msg = {"jsonrpc": "2.0", "method": method, "id": msg_id}
        if params is not None:
            msg["params"] = params
        writer.write(json.dumps(msg).encode() + b"\0")
        await writer.drain()
        while True:
            raw = await asyncio.wait_for(reader.readuntil(b"\0"), self.timeout)
            resp = json.loads(raw[:-1])
            if resp.get("id") != msg_id:
                continue  # unsolicited notification (e.g. EngineStatus)
            if "error" in resp:
                err = resp["error"]
                raise QrcError(f"{method}: {err.get('message')} (code {err.get('code')})")
            return resp.get("result")

    # ---- convenience wrappers ----------------------------------------------

    async def status(self):
        return await self.call("StatusGet", 0)

    async def components(self):
        return await self.call("Component.GetComponents")

    async def controls(self, component: str):
        return await self.call("Component.GetControls", {"Name": component})

    async def get(self, component: str, controls: list[str]):
        return await self.call("Component.Get", {"Name": component, "Controls": [{"Name": c} for c in controls]})

    async def set(self, component: str, control: str, value: Any, ramp: float | None = None):
        ctl: dict[str, Any] = {"Name": control, "Value": value}
        if ramp:
            ctl["Ramp"] = ramp
        return await self.call("Component.Set", {"Name": component, "Controls": [ctl]})

    async def named_get(self, names: list[str]):
        return await self.call("Control.Get", names)

    async def named_set(self, name: str, value: Any, ramp: float | None = None):
        p: dict[str, Any] = {"Name": name, "Value": value}
        if ramp:
            p["Ramp"] = ramp
        return await self.call("Control.Set", p)

    async def watch(self, component: str, controls: list[str], seconds: float, interval: float = 0.25):
        """Sample controls over time (polling Component.Get) for live verification."""
        samples = []
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while loop.time() < end:
            res = await self.get(component, controls)
            samples.append({c["Name"]: c.get("String", c.get("Value")) for c in res.get("Controls", [])})
            await asyncio.sleep(interval)
        return samples
