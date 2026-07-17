from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    @model_validator(mode="after")
    def deadline_precedes_lease(self) -> "Settings":
        if self.rss_total_timeout >= self.worker_lease_seconds:
            raise ValueError("rss_total_timeout must be shorter than worker_lease_seconds")
        return self

    @property
    def allowed_rss_hosts(self) -> frozenset[str]:
        return frozenset(host.strip().lower().rstrip(".") for host in self.rss_allowed_hosts.split(",") if host.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
