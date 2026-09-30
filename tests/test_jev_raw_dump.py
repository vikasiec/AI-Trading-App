"""Tests for JevEvaluator one-shot raw sample dump."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from jev_indstocks_trader.jev_client import JevEvaluator, _strip_secrets


class TestStripSecrets:
    def test_removes_secret_keys(self):
        data = {
            "answers": {"conviction": {"score": 0.8}},
            "api_key": "sk-xxx",
            "auth_token": "tok",
            "account_id": "123",
        }
        result = _strip_secrets(data)
        assert "answers" in result
        assert "api_key" not in result
        assert "auth_token" not in result
        assert "account_id" not in result

    def test_preserves_nested_answers(self):
        data = {
            "answers": {
                "conviction": {"score": 0.8, "confidence": 0.3},
                "noise": {"score": 1.8, "confidence": 0.4},
            },
            "model": "test",
        }
        result = _strip_secrets(data)
        assert result["answers"]["noise"]["score"] == 1.8

    def test_handles_lists(self):
        data = [{"key": "secret", "value": 1}, {"safe": "ok"}]
        result = _strip_secrets(data)
        assert len(result) == 2
        assert "key" not in result[0]
        assert result[1]["safe"] == "ok"


class TestRawDump:
    def _make_evaluator(self, tmp_path: Path) -> JevEvaluator:
        from jev_indstocks_trader.config import JevConfig
        cfg = JevConfig(
            api_key="test-key",
            base_url="http://localhost",
            model="test",
        )
        path = tmp_path / "jev_raw_sample.json"
        return JevEvaluator(cfg, raw_sample_path=path)

    def test_writes_once(self, tmp_path, mocker):
        ev = self._make_evaluator(tmp_path)
        resp_body = {
            "answers": {
                "conviction": {"score": 0.8, "confidence": 0.3},
                "noise": {"score": 1.5, "confidence": 0.5},
            },
            "model": "test",
        }
        mock_resp = Mock()
        mock_resp.json.return_value = resp_body
        mock_resp.raise_for_status = Mock()
        mocker.patch.object(ev.session, "post", return_value=mock_resp)

        ev.evaluate_signal("test context")
        path = tmp_path / "jev_raw_sample.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert "response" in data
        assert "request" in data

        path.write_text("original")
        ev.evaluate_signal("test context 2")
        assert path.read_text() == "original"

    def test_secrets_stripped_from_dump(self, tmp_path, mocker):
        ev = self._make_evaluator(tmp_path)
        resp_body = {
            "answers": {"conviction": {"score": 0.5, "confidence": 0.5}},
            "auth_token": "secret",
        }
        mock_resp = Mock()
        mock_resp.json.return_value = resp_body
        mock_resp.raise_for_status = Mock()
        mocker.patch.object(ev.session, "post", return_value=mock_resp)

        ev.evaluate_signal("ctx")
        data = json.loads((tmp_path / "jev_raw_sample.json").read_text())
        assert "auth_token" not in data.get("response", {})

    def test_unwritable_path_retries_next_call(self, tmp_path, mocker):
        """A failed dump does NOT set _raw_dumped, so the next call retries."""
        ev = self._make_evaluator(tmp_path)
        ev._raw_dumped = False
        resp_body = {
            "answers": {"conviction": {"score": 0.5, "confidence": 0.5}},
        }
        mock_resp = Mock()
        mock_resp.json.return_value = resp_body
        mock_resp.raise_for_status = Mock()
        mocker.patch.object(ev.session, "post", return_value=mock_resp)

        mocker.patch("jev_indstocks_trader.jev_client._strip_secrets", side_effect=RuntimeError("boom"))
        result = ev.evaluate_signal("ctx")
        assert result.score == 0.5
        assert not ev._raw_dumped
