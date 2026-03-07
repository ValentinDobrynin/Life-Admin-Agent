from __future__ import annotations

from google.oauth2.credentials import Credentials

from config import settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
]


def get_credentials() -> Credentials:
    return Credentials(
        token=None,
        refresh_token=settings.google_refresh_token,
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
