"""Tests for the pre-market health check script."""
from __future__ import annotations

from unittest.mock import patch, MagicMock
from scripts.premarket_health import check


def test_check_returns_pass_on_success():
    ok, detail = check("test", lambda: (True, "all good"))
    assert ok is True
    assert detail == "all good"


def test_check_returns_fail_on_false():
    ok, detail = check("test", lambda: (False, "token expired"))
    assert ok is False
    assert detail == "token expired"


def test_check_returns_fail_on_exception():
    def boom():
        raise ConnectionError("unreachable")
    ok, detail = check("test", boom)
    assert ok is False
    assert "unreachable" in detail
