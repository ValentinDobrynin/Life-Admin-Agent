from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str

    telegram_bot_token: str
    telegram_chat_id: int

    openai_api_key: str
    openai_model: str = "gpt-4o"

    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""

    render_url: str = ""
    timezone: str = "Europe/Moscow"
    expiry_window_days: int = 30

    @property
    def is_production(self) -> bool:
        return bool(self.render_url)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


settings = Settings()  # type: ignore[call-arg]
