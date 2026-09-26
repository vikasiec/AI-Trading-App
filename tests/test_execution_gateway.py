from jev_indstocks_trader.execution_gateway import ExecutionGateway, FillResult


def make_gateway(mocker):
    cfg = mocker.Mock(base_url="https://api.indstocks.test")
    return ExecutionGateway(cfg, auth_headers_fn=lambda: {"Authorization": "Bearer x"})


def test_get_order_status_finds_matching_order(mocker):
    gw = make_gateway(mocker)
    mocker.patch.object(
        gw, "get_order_book",
        return_value=[{"order_id": "OID1", "status": "PENDING"}, {"order_id": "OID2", "status": "COMPLETE"}],
    )
    found = gw.get_order_status("OID2")
    assert found == {"order_id": "OID2", "status": "COMPLETE"}


def test_get_order_status_returns_none_when_missing(mocker):
    gw = make_gateway(mocker)
    mocker.patch.object(gw, "get_order_book", return_value=[{"order_id": "OID1", "status": "PENDING"}])
    assert gw.get_order_status("NOPE") is None


def test_wait_for_fill_returns_filled_immediately(mocker):
    gw = make_gateway(mocker)
    mocker.patch.object(
        gw, "get_order_status",
        return_value={"order_id": "OID1", "status": "COMPLETE", "filled_qty": 10, "avg_price": 2452.5},
    )
    result = gw.wait_for_fill("OID1", timeout_s=5.0, poll_interval_s=0.01)
    assert result == FillResult(status="FILLED", filled_qty=10, avg_price=2452.5, raw=result.raw)


def test_wait_for_fill_detects_rejection(mocker):
    gw = make_gateway(mocker)
    mocker.patch.object(gw, "get_order_status", return_value={"order_id": "OID1", "status": "REJECTED"})
    result = gw.wait_for_fill("OID1", timeout_s=5.0, poll_interval_s=0.01)
    assert result.status == "REJECTED"
    assert result.filled_qty == 0
    assert result.avg_price is None


def test_wait_for_fill_polls_until_terminal(mocker):
    gw = make_gateway(mocker)
    responses = [
        {"order_id": "OID1", "status": "PENDING"},
        {"order_id": "OID1", "status": "PENDING"},
        {"order_id": "OID1", "status": "COMPLETE", "filled_qty": 10, "avg_price": 100.0},
    ]
    mocker.patch.object(gw, "get_order_status", side_effect=responses)
    result = gw.wait_for_fill("OID1", timeout_s=5.0, poll_interval_s=0.01)
    assert result.status == "FILLED"
    assert result.avg_price == 100.0


def test_wait_for_fill_times_out_on_persistent_pending(mocker):
    gw = make_gateway(mocker)
    mocker.patch.object(gw, "get_order_status", return_value={"order_id": "OID1", "status": "PENDING"})
    result = gw.wait_for_fill("OID1", timeout_s=0.05, poll_interval_s=0.02)
    assert result.status == "TIMEOUT"
    assert result.avg_price is None


def test_wait_for_fill_not_found_when_order_never_appears(mocker):
    gw = make_gateway(mocker)
    mocker.patch.object(gw, "get_order_status", return_value=None)
    result = gw.wait_for_fill("GHOST", timeout_s=0.05, poll_interval_s=0.02)
    assert result.status == "NOT_FOUND"
    assert result.raw is None


def test_cancel_order_posts_to_order_cancel(mocker):
    gw = make_gateway(mocker)
    resp = mocker.Mock(status_code=200)
    post_mock = mocker.patch("jev_indstocks_trader.execution_gateway.requests.post", return_value=resp)
    gw.cancel_order("OID1")
    post_mock.assert_called_once()
    assert post_mock.call_args.args[0].endswith("/order/cancel")
    assert post_mock.call_args.kwargs["json"]["order_id"] == "OID1"
    assert post_mock.call_args.kwargs["json"]["segment"] == "EQUITY"


def test_extract_order_id_from_common_shapes():
    from jev_indstocks_trader.execution_gateway import extract_order_id
    assert extract_order_id({"data": {"order_id": "A"}}) == "A"
    assert extract_order_id({"data": {"id": "B"}}) == "B"
    assert extract_order_id({"oms_order_id": "C"}) == "C"
    assert extract_order_id({"data": {}}) is None


def test_wait_for_fill_complete_without_filled_qty_is_timeout(mocker):
    gw = make_gateway(mocker)
    mocker.patch.object(
        gw, "get_order_status",
        return_value={"order_id": "OID1", "status": "COMPLETE", "qty": 10},
    )
    result = gw.wait_for_fill("OID1", timeout_s=1.0, poll_interval_s=0.01)
    assert result.status == "TIMEOUT"


def test_wait_for_fill_partial_when_filled_lt_requested(mocker):
    gw = make_gateway(mocker)
    mocker.patch.object(
        gw, "get_order_status",
        return_value={"order_id": "OID1", "status": "COMPLETE", "filled_qty": 3, "avg_price": 100.0},
    )
    result = gw.wait_for_fill("OID1", timeout_s=1.0, poll_interval_s=0.01, requested_qty=10)
    assert result.status == "PARTIAL"
    assert result.filled_qty == 3


def test_wait_for_fill_cancelled_without_fill(mocker):
    gw = make_gateway(mocker)
    mocker.patch.object(gw, "get_order_status", return_value={"order_id": "OID1", "status": "CANCELLED"})
    result = gw.wait_for_fill("OID1", timeout_s=1.0, poll_interval_s=0.01)
    assert result.status == "CANCELLED"


def test_wait_for_fill_missing_order_id(mocker):
    gw = make_gateway(mocker)
    assert gw.wait_for_fill(None).status == "NOT_FOUND"


def test_fill_result_is_a_plain_dataclass():
    fr = FillResult(status="FILLED", filled_qty=5, avg_price=99.5, raw={"x": 1})
    assert fr.status == "FILLED"
    assert fr.filled_qty == 5
    assert fr.avg_price == 99.5
    assert fr.raw == {"x": 1}
