from datetime import timedelta

from vietnam_calendar.jobs import retry_delay


def test_retry_delay_uses_documented_schedule(monkeypatch):
    monkeypatch.setattr("vietnam_calendar.jobs.random.uniform", lambda a, b: 0)
    assert retry_delay(1) == timedelta(minutes=1)
    assert retry_delay(2) == timedelta(minutes=5)
    assert retry_delay(3) == timedelta(minutes=20)
    assert retry_delay(1, 9999) == timedelta(hours=1)
