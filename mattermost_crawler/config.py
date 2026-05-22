"""Configuration loaded from .env and environment variables."""
from __future__ import annotations

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    mm_username: str = ""
    mm_password: str = ""
    mm_base_url: str = "https://mm.schulen-saar.de"

    # Pfad für die persistierte Session (API-Token).
    auth_state_path: Path = Path(".mmauth.json")

    @field_validator("mm_base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return v.rstrip("/")

    def has_credentials(self) -> bool:
        return bool(self.mm_username and self.mm_password)
