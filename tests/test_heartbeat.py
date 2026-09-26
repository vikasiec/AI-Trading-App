from jev_indstocks_trader.heartbeat import BrokerHeartbeat


def _hb(mocker, flatten_on=True):
    flatten = mocker.Mock()
    notifier = mocker.Mock()
    cfg = mocker.Mock(base_url="https://api.indstocks.test")
    return BrokerHeartbeat(
        cfg, lambda: {}, interval_s=0, fails_before_flatten=3,
        flatten_on_outage=flatten_on, flatten_fn=flatten, notifier=notifier,
    ), flatten, notifier


def test_heartbeat_ok_resets_failures(mocker):
    hb, flatten, _ = _hb(mocker)
    resp = mocker.Mock(status_code=200)
    mocker.patch("jev_indstocks_trader.heartbeat.requests.get", return_value=resp)
    assert hb.ping() is True
    assert hb.consecutive_failures == 0
    flatten.assert_not_called()


def test_heartbeat_flatten_after_grace(mocker):
    hb, flatten, notifier = _hb(mocker)
    mocker.patch(
        "jev_indstocks_trader.heartbeat.requests.get",
        side_effect=Exception("down"),
    )
    for _ in range(2):
        assert hb.ping() is False
    flatten.assert_not_called()
    assert hb.ping() is False
    flatten.assert_called_once()
    # one flatten per outage
    assert hb.ping() is False
    flatten.assert_called_once()


def test_heartbeat_paper_does_not_flatten(mocker):
    hb, flatten, _ = _hb(mocker, flatten_on=False)
    mocker.patch(
        "jev_indstocks_trader.heartbeat.requests.get",
        side_effect=Exception("down"),
    )
    for _ in range(5):
        hb.ping()
    flatten.assert_not_called()
