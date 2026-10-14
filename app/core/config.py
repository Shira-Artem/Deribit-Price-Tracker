"""Environment-backed application settings."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: str = "postgresql+psycopg://localhost:5432/deribit_prices"
    celery_broker_url: str = "redis://localhost:6379/0"
    deribit_api_base_url: str = "https://www.deribit.com/api/v2"
    http_timeout_seconds: float = Field(default=5.0, gt=0)
    app_name: str = Field(default="Deribit Price Tracker", min_length=1)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
