import pytest

from jev_indstocks_trader.retry import retry_with_backoff


def test_succeeds_first_try_no_sleep(mocker):
    sleep_mock = mocker.patch("jev_indstocks_trader.retry.time.sleep")
    result = retry_with_backoff(lambda: 42, max_retries=3)
    assert result == 42
    sleep_mock.assert_not_called()


def test_retries_then_succeeds(mocker):
    mocker.patch("jev_indstocks_trader.retry.time.sleep")
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("transient")
        return "ok"

    result = retry_with_backoff(flaky, max_retries=5, retry_on=(ValueError,))
    assert result == "ok"
    assert calls["n"] == 3


def test_raises_after_exhausting_retries(mocker):
    mocker.patch("jev_indstocks_trader.retry.time.sleep")

    def always_fails():
        raise ValueError("permanent")

    with pytest.raises(ValueError):
        retry_with_backoff(always_fails, max_retries=3, retry_on=(ValueError,))


def test_does_not_catch_unlisted_exceptions(mocker):
    mocker.patch("jev_indstocks_trader.retry.time.sleep")

    def raises_type_error():
        raise TypeError("not retryable here")

    with pytest.raises(TypeError):
        retry_with_backoff(raises_type_error, max_retries=3, retry_on=(ValueError,))
