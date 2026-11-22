"""Application settings, read from environment variables or a .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

IST = ZoneInfo("Asia/Kolkata")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Web
    app_user: str = "admin"
    app_password: SecretStr = SecretStr("")
    session_secret: SecretStr = SecretStr("")
    cookie_secure: bool = True
    app_url: str = "http://localhost:8000"

    # Storage
    database_url: str = "sqlite:///data/app.db"
    data_dir: Path = Path("data")

    # AI
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.5-flash"
    gemini_daily_call_budget: int = Field(200, ge=0)
    gemini_min_interval_seconds: float = Field(6.0, ge=0)

    # Research and picks
    shortlist_per_group: int = Field(10, ge=1, le=50)  # per size group: large, mid-size, small
    picks_per_group: int = Field(3, ge=1, le=10)
    pick_min_rating: float = Field(3.5, ge=1, le=5)
    min_turnover_cr: float = Field(5.0, ge=0)
    max_pledge_pct: float = Field(25.0, ge=0, le=100)
    research_batch_size: int = Field(4, ge=1, le=50)
    on_demand_daily_limit: int = Field(10, ge=0)

    # Costs
    brokerage_per_order: float = Field(20.0, ge=0)
    dp_charge_per_sell: float = Field(15.93, ge=0)
    short_term_tax_rate: float = Field(0.20, ge=0, le=1)
    long_term_tax_rate: float = Field(0.125, ge=0, le=1)
    reference_investment: float = Field(40000.0, gt=0)  # typical real trade size, used for cost estimates
    practice_amount: float = Field(10000.0, gt=0)  # pretend money put into every pick

    # Notifications
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    email_to: str = ""
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: str = ""

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key.get_secret_value())

    @property
    def email_enabled(self) -> bool:
        return bool(self.smtp_user and self.smtp_password.get_secret_value() and self.email_to)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token.get_secret_value() and self.telegram_chat_id)

    @property
    def login_configured(self) -> bool:
        return len(self.app_password.get_secret_value()) >= 12 and len(self.session_secret.get_secret_value()) >= 32


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
