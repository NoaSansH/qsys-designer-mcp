import asyncio
import json

import pytest

from qsys_mcp.qrc.client import QrcClient, QrcError


async def fake_core(handler):
    """Start a NUL-delimited JSON-RPC server; handler(msg) -> result or Exception."""

    async def on_conn(reader, writer):
        # Unsolicited notification first, like a real Core
        writer.write(json.dumps({"jsonrpc": "2.0", "method": "EngineStatus", "params": {}}).encode() + b"\0")
        while True:
            try:
                raw = await reader.readuntil(b"\0")
            except asyncio.IncompleteReadError:
                break
            msg = json.loads(raw[:-1])
            out = handler(msg)
            resp = {"jsonrpc": "2.0", "id": msg["id"]}
            if isinstance(out, Exception):
                resp["error"] = {"code": 8, "message": str(out)}
            else:
                resp["result"] = out
            writer.write(json.dumps(resp).encode() + b"\0")
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(on_conn, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.asyncio
async def test_set_and_get():
    seen = []

    def handler(msg):
        seen.append(msg)
        if msg["method"] == "Component.Get":
            return {"Name": "Gain1", "Controls": [{"Name": "gain", "Value": -6.0, "String": "-6.0dB"}]}
        return True

    server, port = await fake_core(handler)
    async with server:
        c = QrcClient(port=port)
        assert await c.set("Gain1", "gain", -6, ramp=2) is True
        res = await c.get("Gain1", ["gain"])
        assert res["Controls"][0]["Value"] == -6.0
    assert seen[0]["params"] == {"Name": "Gain1", "Controls": [{"Name": "gain", "Value": -6, "Ramp": 2}]}


@pytest.mark.asyncio
async def test_error_and_logon():
    def handler(msg):
        if msg["method"] == "Logon":
            return True
        return ValueError("Unknown component")

    server, port = await fake_core(handler)
    async with server:
        c = QrcClient(port=port, user="u", password="p")
        with pytest.raises(QrcError, match="Unknown component"):
            await c.controls("Nope")


@pytest.mark.asyncio
async def test_unreachable():
    with pytest.raises(QrcError, match="Cannot reach"):
        await QrcClient(port=1, timeout=1).status()
