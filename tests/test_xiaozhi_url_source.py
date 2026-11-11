import asyncio

from core.xiaozhi_client import XiaozhiClient


def test_url_source_reevaluated_each_attempt_and_kick_skips_backoff(monkeypatch):
    urls = iter(["ws://lan:8000/api/ws", "ws://100.64.0.10:8000/api/ws",
                 "ws://100.64.0.10:8000/api/ws"])
    tried = []

    async def fake_connect(url, **kw):
        tried.append(url)
        raise OSError("unreachable")

    import core.xiaozhi_client as xc
    monkeypatch.setattr(xc.websockets, "connect", fake_connect)

    async def run():
        c = XiaozhiClient({}, reconnect_backoff_base=30.0)
        c.start(lambda: next(urls))
        await asyncio.sleep(0.05)          # first attempt, then 30 s backoff
        assert tried == ["ws://lan:8000/api/ws"]
        c.start(lambda: "ws://100.64.0.10:8000/api/ws")   # network changed
        await asyncio.sleep(0.05)
        assert tried[-1] == "ws://100.64.0.10:8000/api/ws"  # no 30 s wait
        await c.disconnect()

    asyncio.run(run())
