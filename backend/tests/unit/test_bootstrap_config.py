import pytest
import os, subprocess, sys
from vietnam_calendar.bootstrap import bootstrap_admin
from vietnam_calendar.config import Settings, get_settings

@pytest.mark.asyncio
async def test_bootstrap_rejects_invalid_argon2id_before_database(monkeypatch):
    settings=Settings(admin_password_hash="plaintext",database_url="postgresql+psycopg://ignored:ignored@127.0.0.1:1/ignored")
    monkeypatch.setattr("vietnam_calendar.bootstrap.get_settings",lambda:settings)
    with pytest.raises(RuntimeError,match="valid Argon2id"): await bootstrap_admin()

def test_bootstrap_cli_bad_hash_exits_nonzero():
    env={**os.environ,"ADMIN_PASSWORD_HASH":"plaintext"}
    result=subprocess.run([sys.executable,"-m","vietnam_calendar.bootstrap"],env=env,capture_output=True,text=True)
    assert result.returncode != 0; assert "valid Argon2id" in result.stderr
