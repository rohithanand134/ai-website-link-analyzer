from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-6-luna"
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.1-flash-lite"
    brave_search_api_key: SecretStr | None = None
    competitor_research: Literal["off", "wikidata"] = "wikidata"
    analysis_per_day: int = Field(120, ge=1, le=10_000)
    analysis_per_minute: int = Field(12, ge=1, le=300)
    analysis_per_caller_per_minute: int = Field(5, ge=1, le=300)
    analysis_max_in_flight: int = Field(3, ge=1, le=32)
    llm_price_model: str = ""
    llm_input_usd_per_million: Decimal | None = Field(default=None, ge=0)
    llm_output_usd_per_million: Decimal | None = Field(default=None, ge=0)
    llm_timeout_seconds: float = Field(60, gt=0, le=120)
    http_timeout_seconds: float = Field(15, gt=0, le=30)
    max_website_chars: int = Field(16_000, ge=4096, le=32_000)
    max_crawl_pages: int = Field(4, ge=1, le=4)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
