import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

REQUIRED_ENV = {
    "INDSTOCKS_CLIENT_ID": "test-client-id",
    "INDSTOCKS_MPIN": "0000",
    "INDSTOCKS_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
    "JEV_API_KEY": "test-jev-key",
    "TELEGRAM_BOT_TOKEN": "123:test-token",
    "TELEGRAM_OWNER_CHAT_ID": "999999",
}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for k, v in REQUIRED_ENV.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("INDSTOCKS_TOKEN_CACHE", str(tmp_path / "session_token.json"))
    monkeypatch.setenv("PAPER_TRADING", "true")
    yield
