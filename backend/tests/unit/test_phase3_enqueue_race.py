import uuid
from unittest.mock import AsyncMock
import pytest
import vietnam_calendar.api as api
from vietnam_calendar.models import JobType

class Scalars:
    def __init__(self,value): self.value=value
    def one_or_none(self): return self.value

@pytest.mark.asyncio
async def test_enqueue_completion_race_retries_insert_and_reports_created(monkeypatch):
    created_id=uuid.uuid4(); monkeypatch.setattr(api,"enqueue",AsyncMock(side_effect=[None,created_id]))
    db=AsyncMock(); db.scalars=AsyncMock(return_value=Scalars(None))
    job_id,created=await api.enqueue_or_active(db,JobType.retention,{"kind":"importance_eval"},"eval:key",max_attempts=2)
    assert job_id==created_id and created is True

@pytest.mark.asyncio
async def test_enqueue_completion_race_finds_new_active_row(monkeypatch):
    active_id=uuid.uuid4(); monkeypatch.setattr(api,"enqueue",AsyncMock(side_effect=[None,None]))
    db=AsyncMock(); db.scalars=AsyncMock(side_effect=[Scalars(None),Scalars(active_id)])
    job_id,created=await api.enqueue_or_active(db,JobType.reanalyze_event,{"event_id":str(uuid.uuid4())},"event:key",max_attempts=2)
    assert job_id==active_id and created is False
