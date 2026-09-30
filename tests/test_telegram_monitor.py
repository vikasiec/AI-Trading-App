"""Tests for Telegram monitoring commands (/status, /watchlist, /audit, /logs, /health)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jev_indstocks_trader.telegram_alerts import (
    TelegramAlertNotifier,
    _authorized_read,
    _read_last_n_audit,
    _read_today_audit,
)


@pytest.fixture
def notifier():
    cfg = MagicMock()
    cfg.bot_token = "fake:token"
    cfg.owner_chat_id = 123
    cfg.owner_user_id = 456
    with patch("jev_indstocks_trader.telegram_alerts.telebot.TeleBot"):
        n = TelegramAlertNotifier(cfg, kill_switch_callback=lambda: None)
    return n


@pytest.fixture
def state_provider(notifier):
    state = {
        "paper_trading": True,
        "halted": False,
        "positions": [],
        "watchlist": ["RELIANCE", "TCS", "INFY"],
        "jev_calls_today": 42,
        "jev_cap": 500,
        "started_at": time.time() - 3700,
        "audit_path": "",
        "heartbeat_ok": True,
        "heartbeat_consecutive_failures": 0,
        "telegram_alive": True,
    }
    notifier.set_state_provider(lambda: dict(state))
    return state


class TestAuthorizedRead:
    def test_owner_allowed(self):
        msg = MagicMock()
        msg.chat.id = 123
        msg.forward_from = None
        msg.forward_from_chat = None
        msg.forward_origin = None
        msg.forward_date = None
        assert _authorized_read(msg, 123) is True

    def test_wrong_chat_rejected(self):
        msg = MagicMock()
        msg.chat.id = 999
        msg.forward_from = None
        msg.forward_from_chat = None
        msg.forward_origin = None
        msg.forward_date = None
        assert _authorized_read(msg, 123) is False

    def test_forwarded_rejected(self):
        msg = MagicMock()
        msg.chat.id = 123
        msg.forward_from = MagicMock()
        assert _authorized_read(msg, 123) is False


class TestFormatStatus:
    def test_no_state_provider(self, notifier):
        result = notifier._format_status()
        assert "not wired" in result

    def test_basic_status(self, notifier, state_provider):
        result = notifier._format_status()
        assert "RUNNING" in result
        assert "PAPER" in result
        assert "42/500" in result
        assert "1h" in result

    def test_halted_status(self, notifier, state_provider):
        state_provider["halted"] = True
        notifier.set_state_provider(lambda: dict(state_provider))
        result = notifier._format_status()
        assert "HALTED" in result

    def test_with_positions(self, notifier, state_provider):
        pos = MagicMock()
        pos.symbol = "RELIANCE"
        pos.security_id = "NSE_2885"
        pos.entry_price = 2500.0
        pos.stop_loss_price = 2475.0
        pos.target_price = 2550.0
        pos.qty = 20
        pos.cumulative_pnl = 150.0
        state_provider["positions"] = [pos]
        notifier.set_state_provider(lambda: dict(state_provider))
        result = notifier._format_status()
        assert "RELIANCE" in result
        assert "2500.00" in result
        assert "+150.00" in result


class TestFormatWatchlist:
    def test_empty(self, notifier, state_provider):
        state_provider["watchlist"] = []
        notifier.set_state_provider(lambda: dict(state_provider))
        result = notifier._format_watchlist()
        assert "empty" in result

    def test_with_symbols(self, notifier, state_provider):
        result = notifier._format_watchlist()
        assert "RELIANCE" in result
        assert "TCS" in result
        assert "(3)" in result


class TestFormatHealth:
    def test_healthy(self, notifier, state_provider):
        result = notifier._format_health()
        assert "PAPER" in result
        assert "Heartbeat: OK" in result
        assert "alive" in result

    def test_heartbeat_failing(self, notifier, state_provider):
        state_provider["heartbeat_ok"] = False
        state_provider["heartbeat_consecutive_failures"] = 3
        notifier.set_state_provider(lambda: dict(state_provider))
        result = notifier._format_health()
        assert "FAIL" in result
        assert "3" in result


class TestReadAuditFiles:
    def test_read_today_audit(self, tmp_path):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        audit_file = tmp_path / "audit.jsonl"
        lines = [
            json.dumps({"ts": f"{today}T09:30:00+00:00", "action": "BUY", "symbol": "TCS"}),
            json.dumps({"ts": "2025-01-01T09:30:00+00:00", "action": "BUY", "symbol": "OLD"}),
            json.dumps({"ts": f"{today}T10:00:00+00:00", "action": "SKIP", "symbol": "INFY"}),
        ]
        audit_file.write_text("\n".join(lines) + "\n")
        entries = _read_today_audit(str(audit_file))
        assert len(entries) == 2
        assert entries[0]["symbol"] == "TCS"
        assert entries[1]["symbol"] == "INFY"

    def test_read_last_n(self, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        lines = [
            json.dumps({"ts": f"2026-09-30T{i:02d}:00:00+00:00", "action": "SKIP", "symbol": f"S{i}"})
            for i in range(20)
        ]
        audit_file.write_text("\n".join(lines) + "\n")
        entries = _read_last_n_audit(str(audit_file), n=5)
        assert len(entries) == 5
        assert entries[0]["symbol"] == "S15"

    def test_missing_file(self):
        assert _read_today_audit("/nonexistent/path.jsonl") == []
        assert _read_last_n_audit("/nonexistent/path.jsonl") == []


class TestFormatAudit:
    def test_no_entries(self, notifier, state_provider, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        audit_file.write_text("")
        state_provider["audit_path"] = str(audit_file)
        notifier.set_state_provider(lambda: dict(state_provider))
        result = notifier._format_audit()
        assert "No audit entries" in result

    def test_with_entries(self, notifier, state_provider, tmp_path):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).date().isoformat()
        audit_file = tmp_path / "audit.jsonl"
        entries = [
            {"ts": f"{today}T09:30:00+00:00", "action": "BUY", "symbol": "TCS",
             "jev_conviction": 0.85, "jev_confidence": 0.72, "security_id": "NSE_1234"},
            {"ts": f"{today}T09:31:00+00:00", "action": "SKIP", "symbol": "INFY",
             "skip_reason": "below_threshold", "security_id": "NSE_5678"},
        ]
        audit_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        state_provider["audit_path"] = str(audit_file)
        notifier.set_state_provider(lambda: dict(state_provider))
        result = notifier._format_audit()
        assert "1 BUY" in result
        assert "1 SKIP" in result
        assert "TCS" in result
        assert "below_threshold" in result


class TestFormatLogs:
    def test_with_entries(self, notifier, state_provider, tmp_path):
        audit_file = tmp_path / "audit.jsonl"
        entries = [
            {"ts": "2026-09-30T09:30:00+00:00", "action": "BUY", "symbol": "TCS",
             "jev_conviction": 0.85, "security_id": "NSE_1234"},
            {"ts": "2026-09-30T10:00:00+00:00", "type": "outcome_update",
             "decision_id": "NSE_1234_12345", "net_pnl": 120.5, "exit_reason": "target"},
        ]
        audit_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        state_provider["audit_path"] = str(audit_file)
        notifier.set_state_provider(lambda: dict(state_provider))
        result = notifier._format_logs()
        assert "BUY" in result
        assert "EXIT" in result
        assert "target" in result
        assert "+120.50" in result
