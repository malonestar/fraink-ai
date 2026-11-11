import asyncio
import json

import pytest
import websockets

from core.xiaozhi_client import XiaozhiClient


def make_client(**kw):
    config = {"server": {"device_id": "test", "client_id": "test", "protocol_version": 1}}
    return XiaozhiClient(config, **kw)


async def _serve(handler):
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    return server, f"ws://127.0.0.1:{port}/api/ws"


@pytest.mark.asyncio
async def test_reconnects_after_connection_drop():
    connect_count = 0
    second_connected = asyncio.Event()

    async def handler(ws):
        nonlocal connect_count
        connect_count += 1
        n = connect_count
        await ws.recv()  # client hello
        await ws.send(json.dumps({
            "type": "hello", "version": 1, "session_id": f"s{n}",
            "audio_params": {"sample_rate": 24000},
        }))
        if n == 1:
            await ws.close()  # simulate a server-side drop right after handshake
        else:
            second_connected.set()
            await asyncio.sleep(5)

    server, url = await _serve(handler)
    client = make_client(reconnect_backoff_base=0.05)
    client.start(url)
    try:
        await asyncio.wait_for(second_connected.wait(), timeout=5)
        await asyncio.sleep(0.1)
        assert client.connected is True
        assert client.session_id == "s2"
        assert connect_count >= 2
    finally:
        await client.disconnect()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_disconnect_stops_reconnection():
    connect_count = 0

    async def handler(ws):
        nonlocal connect_count
        connect_count += 1
        await ws.recv()
        await ws.send(json.dumps({
            "type": "hello", "session_id": "s", "audio_params": {"sample_rate": 24000},
        }))
        await ws.close()  # always drop -> client keeps reconnecting

    server, url = await _serve(handler)
    client = make_client(reconnect_backoff_base=0.05)
    client.start(url)
    await asyncio.sleep(0.3)  # connect + drop + reconnect a few times
    await client.disconnect()
    count_after = connect_count
    await asyncio.sleep(0.3)
    assert connect_count == count_after  # no further connects after disconnect
    assert client.connected is False
    server.close()
    await server.wait_closed()
