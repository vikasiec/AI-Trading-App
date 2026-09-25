import json
import time

from jev_indstocks_trader import auth
from jev_indstocks_trader.config import load_config


def test_write_and_read_cache_roundtrip(tmp_path, mocker):
    cfg = load_config()
    a = auth.INDstocksAuth(cfg.indstocks)

    mock_resp = mocker.Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"data": {"token": "abc123"}}
    mocker.patch("jev_indstocks_trader.auth.requests.post", return_value=mock_resp)
    mocker.patch("jev_indstocks_trader.auth.pyotp.TOTP.now", return_value="000000")

    token = a.refresh_session()
    assert token == "abc123"

    read_back = auth.get_cached_token(cfg.indstocks)
    assert read_back == "abc123"


def test_stale_token_logs_warning(tmp_path, mocker, caplog):
    cfg = load_config()
    cfg.indstocks.token_cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cfg.indstocks.token_cache_path, "w") as f:
        json.dump({"token": "old-token", "issued_at": time.time() - 25 * 3600}, f)

    with caplog.at_level("WARNING"):
        token = auth.get_cached_token(cfg.indstocks)

    assert token == "old-token"
    assert any("old" in r.message for r in caplog.records)
