from __future__ import annotations

import os

# Must be set at module level, before any app module is imported,
# so that Settings() can read them during collection.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test:token123")
os.environ.setdefault("TELEGRAM_CHAT_ID", "123456789")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-key")
os.environ.setdefault("OPENAI_MODEL", "gpt-4o")
os.environ.setdefault("MAILGUN_API_KEY", "test-mailgun-key")
os.environ.setdefault("MAILGUN_DOMAIN", "test.example.com")
os.environ.setdefault("R2_ACCOUNT_ID", "test-account-id")
os.environ.setdefault("R2_ACCESS_KEY_ID", "test-access-key")
os.environ.setdefault("R2_SECRET_ACCESS_KEY", "test-secret-key")
os.environ.setdefault("R2_BUCKET_NAME", "test-bucket")
os.environ.setdefault("GOOGLE_CLIENT_ID", "test-client-id")
os.environ.setdefault("GOOGLE_CLIENT_SECRET", "test-client-secret")
os.environ.setdefault("GOOGLE_REFRESH_TOKEN", "test-refresh-token")
os.environ.setdefault("API_KEY", "test-api-key")
