from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", ".env.local"), extra="ignore")

    app_env: str = "development"
    database_url: str = "postgresql+psycopg://app:change-me@db:5432/vietnam_calendar"
    session_cookie_name: str = "vc_session"
    session_ttl_seconds: int = Field(default=43200, ge=300, le=604800)
    cookie_secure: bool = False
    admin_username: str = "admin"
    admin_password_hash: str = ""
    rss_allowed_hosts: str = "news.tuoitre.vn"
    rss_user_agent: str = "VietnamCalendar/0.1"
    rss_contact: str = ""
    rss_max_body_bytes: int = Field(default=5 * 1024 * 1024, ge=1024, le=20 * 1024 * 1024)
    rss_connect_timeout: float = Field(default=5.0, gt=0, le=60)
    rss_read_timeout: float = Field(default=20.0, gt=0, le=120)
    rss_write_timeout: float = Field(default=10.0, gt=0, le=60)
    rss_pool_timeout: float = Field(default=5.0, gt=0, le=60)
    rss_total_timeout: float = Field(default=90.0, gt=0, le=300)
    rss_max_entries: int = Field(default=500, ge=1, le=5000)
    rss_max_raw_entry_bytes: int = Field(default=65536, ge=1024, le=1048576)
    worker_poll_seconds: float = Field(default=2.0, ge=0.1, le=60)
    worker_lease_seconds: int = Field(default=120, ge=30, le=3600)
    ai_provider: str = "disabled"
    ai_fallback_provider: str = "disabled"
    ai_auto_fallback: bool = False
    ai_timeout_seconds: float = Field(default=45, gt=0, le=180)
    openai_api_key: str = Field(default="", repr=False)
    openai_model: str = ""
    openai_base_url: str = "https://api.openai.com"
    openai_allow_unsafe_base_url: bool = False
    ollama_model: str = ""
    ollama_base_url: str = "http://ollama:11434"

    @model_validator(mode="after")
    def deadline_precedes_lease(self) -> "Settings":
        if self.rss_total_timeout >= self.worker_lease_seconds:
            raise ValueError("rss_total_timeout must be shorter than worker_lease_seconds")
        from urllib.parse import urlsplit
        u=urlsplit(self.openai_base_url)
        if self.openai_api_key and not self.openai_allow_unsafe_base_url and (u.scheme!="https" or (u.hostname or "").lower()!="api.openai.com"):
            raise ValueError("OPENAI_API_KEY may only be sent to https://api.openai.com; development override must be explicit")
        return self

    @property
    def allowed_rss_hosts(self) -> frozenset[str]:
        return frozenset(host.strip().lower().rstrip(".") for host in self.rss_allowed_hosts.split(",") if host.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
