from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Database
    database_url: str

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: int

    # OpenAI
    openai_api_key: str
    openai_model: str = "gpt-4o"

    # Mailgun
    mailgun_api_key: str = ""
    mailgun_domain: str = ""

    # Cloudflare R2
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""

    # Google Calendar
    google_client_id: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""

    # API auth
    api_key: str = ""

    # App
    render_url: str = ""
    timezone: str = "Europe/Moscow"
    scheduler_test_mode: bool = False

    @property
    def is_production(self) -> bool:
        return bool(self.render_url)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()
