from __future__ import annotations


def test_settings_loads_from_env() -> None:
    from config import settings

    assert settings.telegram_bot_token == "test:token123"
    assert settings.telegram_chat_id == 123456789
    assert settings.openai_api_key == "sk-test-key"
    assert settings.openai_model == "gpt-4o"
    assert settings.timezone == "Europe/Moscow"


def test_settings_defaults() -> None:
    from config import settings

    assert settings.mailgun_api_key == "test-mailgun-key"
    assert settings.r2_bucket_name == "test-bucket"
    assert settings.api_key == "test-api-key"


def test_is_production_false_without_render_url() -> None:
    from config import settings

    assert settings.is_production is False


def test_database_url_set() -> None:
    from config import settings

    assert "sqlite" in settings.database_url
