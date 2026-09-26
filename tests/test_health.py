import json
import logging

from jev_indstocks_trader.health import build_payload
from jev_indstocks_trader.slog import JsonFormatter, configure_logging


def test_health_payload_shape():
    p = build_payload(paper_trading=True, open_positions=2, watchlist_n=4, started_at=0)
    assert p["ok"] is True
    assert p["paper_trading"] is True
    assert p["open_positions"] == 2
    assert p["watchlist"] == 4
    assert "uptime_s" in p


def test_json_formatter_emits_level_and_msg():
    rec = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %s", ("x",), None)
    line = JsonFormatter().format(rec)
    data = json.loads(line)
    assert data["level"] == "INFO"
    assert data["msg"] == "hello x"
    assert data["logger"] == "t"


def test_non_loopback_health_requires_token():
    from jev_indstocks_trader.health import start_health_server
    try:
        start_health_server(lambda: {"ok": True}, host="0.0.0.0", port=0, token="")
        assert False, "should have refused"
    except RuntimeError:
        pass


def test_configure_logging_json_mode_does_not_raise():
    configure_logging(json_mode=True)
    logging.getLogger("t").info("ok")
