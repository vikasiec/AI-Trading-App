from jev_indstocks_trader import gtt_orders
from jev_indstocks_trader.config import load_config


def test_place_gtt_oco_returns_id(mocker):
    cfg = load_config()
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"data": {"gtt_id": "GTT123"}}
    mocker.patch("jev_indstocks_trader.gtt_orders.requests.post", return_value=mock_resp)

    gtt_id = gtt_orders.place_gtt_oco(
        cfg.indstocks, auth_headers_fn=lambda: {}, security_id="2885", exchange="NSE",
        segment="EQUITY", qty=10, stop_loss_trigger=2400.0, target_trigger=2500.0,
    )
    assert gtt_id == "GTT123"


def test_place_gtt_oco_payload_shape(mocker):
    cfg = load_config()
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"data": {"gtt_id": "GTT123"}}
    post_mock = mocker.patch("jev_indstocks_trader.gtt_orders.requests.post", return_value=mock_resp)

    gtt_orders.place_gtt_oco(
        cfg.indstocks, auth_headers_fn=lambda: {}, security_id="2885", exchange="NSE",
        segment="EQUITY", qty=10, stop_loss_trigger=2400.0, target_trigger=2500.0,
    )
    _, kwargs = post_mock.call_args
    payload = kwargs["json"]
    assert payload["security_id"] == "2885"
    assert payload["type"] == "GTT_OCO"
    leg_types = {leg["type"] for leg in payload["legs"]}
    assert leg_types == {"STOP_LOSS", "TARGET"}


def test_cancel_gtt_does_not_raise_on_failure(mocker):
    cfg = load_config()
    mocker.patch(
        "jev_indstocks_trader.gtt_orders.requests.delete",
        side_effect=RuntimeError("network error"),
    )
    # should log and swallow, never raise -- a failed cancel shouldn't block the exit that already happened
    gtt_orders.cancel_gtt(cfg.indstocks, auth_headers_fn=lambda: {}, gtt_id="GTT123")


def test_cancel_gtt_calls_delete_with_id(mocker):
    cfg = load_config()
    mock_resp = mocker.Mock()
    mock_resp.raise_for_status.return_value = None
    delete_mock = mocker.patch("jev_indstocks_trader.gtt_orders.requests.delete", return_value=mock_resp)

    gtt_orders.cancel_gtt(cfg.indstocks, auth_headers_fn=lambda: {}, gtt_id="GTT123")
    args, _ = delete_mock.call_args
    assert "GTT123" in args[0]
