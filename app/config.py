from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = Field(alias="BOT_TOKEN")
    base_url: str = Field(default="http://localhost", alias="BASE_URL")
    tg_webhook_path: str = Field(default="/webhook/telegram", alias="TG_WEBHOOK_PATH")
    payment_webhook_path: str = Field(default="/webhook/payment", alias="PAYMENT_WEBHOOK_PATH")

    db_url: str = Field(alias="DB_URL")
    admins: List[int] = Field(default_factory=list, alias="ADMINS")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    default_lang: str = Field(default="ru", alias="DEFAULT_LANG")
    timezone: str = Field(default="Europe/Moscow", alias="TIMEZONE")

    support_username: str = Field(default="datooff", alias="SUPPORT_USERNAME")
    max_requests_per_minute: int = Field(default=20, alias="MAX_REQUESTS_PER_MINUTE")
    backup_dir: str = Field(default="./backups", alias="BACKUP_DIR")

    telegram_insecure_ssl: bool = Field(default=False, alias="TELEGRAM_INSECURE_SSL")
    telegram_force_tls12: bool = Field(default=False, alias="TELEGRAM_FORCE_TLS12")
    telegram_ipv4_only: bool = Field(default=False, alias="TELEGRAM_IPV4_ONLY")
    telegram_proxy: Optional[str] = Field(default=None, alias="TELEGRAM_PROXY")

    @field_validator("telegram_proxy", mode="before")
    @classmethod
    def empty_proxy(cls, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str) and not value.strip():
            return None
        return str(value).strip()

    @field_validator("admins", mode="before")
    @classmethod
    def parse_admins(cls, value: str | int | list[int] | None) -> list[int]:
        if isinstance(value, list):
            return value
        if isinstance(value, int):
            return [value]
        if not value:
            return []
        return [int(v.strip()) for v in str(value).split(",") if v.strip()]

    @property
    def telegram_webhook_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{self.tg_webhook_path}"

    @property
    def backup_dir_path(self) -> Path:
        path = Path(self.backup_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def primary_admin_id(self) -> int | None:
        return self.admins[0] if self.admins else None


settings = Settings()
