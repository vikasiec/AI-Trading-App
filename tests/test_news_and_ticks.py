import time

from jev_indstocks_trader.market_data import LiveTickCache
from jev_indstocks_trader.news_feed import NewsSource


# -- NewsSource --------------------------------------------------

def _patch_feed(mocker, entries):
    resp = mocker.Mock()
    resp.content = b"<rss/>"
    resp.raise_for_status = mocker.Mock()
    get_mock = mocker.patch(
        "jev_indstocks_trader.news_feed.requests.get",
        return_value=resp,
    )
    parse_mock = mocker.patch(
        "jev_indstocks_trader.news_feed.feedparser.parse",
        return_value=mocker.Mock(entries=entries, bozo=False),
    )
    return get_mock, parse_mock


def test_headlines_for_filters_by_symbol(mocker):
    fake_entries = [
        {"title": "Reliance posts record profit", "summary": ""},
        {"title": "Unrelated company news", "summary": "mentions reliance in passing"},
        {"title": "TCS announces buyback", "summary": ""},
        {"title": "RELIANCEFO contract note", "summary": ""},
    ]
    _patch_feed(mocker, fake_entries)
    source = NewsSource(["http://example.com/feed"])
    headlines = source.headlines_for("RELIANCE")
    assert "Reliance posts record profit" in headlines
    assert "Unrelated company news" in headlines  # matched via summary
    assert "TCS announces buyback" not in headlines
    assert "RELIANCEFO contract note" not in headlines


def test_headlines_for_respects_limit(mocker):
    fake_entries = [{"title": f"Reliance news {i}", "summary": ""} for i in range(10)]
    _patch_feed(mocker, fake_entries)
    source = NewsSource(["http://example.com/feed"])
    headlines = source.headlines_for("RELIANCE", limit=3)
    assert len(headlines) == 3


def test_feed_is_cached_within_ttl(mocker):
    get_mock, parse_mock = _patch_feed(
        mocker, [{"title": "Reliance up", "summary": ""}]
    )
    source = NewsSource(["http://example.com/feed"], cache_ttl_s=300)
    source.headlines_for("RELIANCE")
    source.headlines_for("RELIANCE")
    parse_mock.assert_called_once()
    get_mock.assert_called_once()


def test_feed_timeout_returns_empty(mocker):
    mocker.patch(
        "jev_indstocks_trader.news_feed.requests.get",
        side_effect=TimeoutError("stalled"),
    )
    source = NewsSource(["http://example.com/feed"])
    assert source.headlines_for("RELIANCE") == []


def test_feed_parse_failure_returns_empty_not_raises(mocker):
    mocker.patch(
        "jev_indstocks_trader.news_feed.feedparser.parse", side_effect=RuntimeError("network error")
    )
    source = NewsSource(["http://example.com/feed"])
    assert source.headlines_for("RELIANCE") == []


# -- LiveTickCache --------------------------------------------------

def test_fresh_tick_returned():
    cache = LiveTickCache(max_staleness_s=5.0)
    cache.update("NSE_2885", 2456.75)
    assert cache.get_fresh("NSE_2885") == 2456.75


def test_stale_tick_returns_none():
    cache = LiveTickCache(max_staleness_s=0.01)
    cache.update("NSE_2885", 2456.75)
    time.sleep(0.05)
    assert cache.get_fresh("NSE_2885") is None


def test_unknown_symbol_returns_none():
    cache = LiveTickCache()
    assert cache.get_fresh("NSE_9999") is None
