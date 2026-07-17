import pytest

import vietnam_calendar.worker as worker


class Context:
    async def __aenter__(self): return object()
    async def __aexit__(self,*args): return False


@pytest.mark.asyncio
async def test_heartbeat_db_error_is_retryable_unavailable(monkeypatch):
    async def no_sleep(seconds): return None
    async def broken(*args,**kwargs): raise OSError("db down")
    monkeypatch.setattr(worker.asyncio,"sleep",no_sleep)
    monkeypatch.setattr(worker,"SessionFactory",lambda:Context())
    monkeypatch.setattr(worker,"heartbeat",broken)
    with pytest.raises(worker.HeartbeatUnavailable): await worker._heartbeat_loop(__import__("uuid").uuid4(),"owner",30)


@pytest.mark.asyncio
async def test_heartbeat_definite_ownership_loss(monkeypatch):
    async def no_sleep(seconds): return None
    async def lost(*args,**kwargs): return False
    class Session:
        async def commit(self): pass
    class SessionContext:
        async def __aenter__(self): return Session()
        async def __aexit__(self,*args): return False
    monkeypatch.setattr(worker.asyncio,"sleep",no_sleep)
    monkeypatch.setattr(worker,"SessionFactory",lambda:SessionContext())
    monkeypatch.setattr(worker,"heartbeat",lost)
    with pytest.raises(worker.LeaseLost): await worker._heartbeat_loop(__import__("uuid").uuid4(),"owner",30)
