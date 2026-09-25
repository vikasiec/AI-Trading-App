"""News/headline ingestion via RSS.

Replaces the always-empty `headlines` in feature_prep.MarketSnapshot.
Pulls from one or more configurable RSS/Atom feeds (NEWS_RSS_FEEDS, comma-
separated URLs -- market news feeds, corporate announcement feeds, whatever
you point it at), filters entries whose title/summary mentions the symbol
or company name, and caches per-feed results for NEWS_CACHE_TTL_S seconds
so every symbol lookup on every tick doesn't refetch every feed.

This is intentionally source-agnostic -- plug in whatever feeds you trust;
nothing here is INDstocks- or NSE-specific.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import feedparser

logger = logging.getLogger(__name__)


@dataclass
class _CachedFeed:
    fetched_at: float
    entries: list[dict]


class NewsSource:
    def __init__(self, feed_urls: list[str], cache_ttl_s: float = 300.0):
        self.feed_urls = feed_urls
        self.cache_ttl_s = cache_ttl_s
        self._cache: dict[str, _CachedFeed] = {}

    def _fetch_feed(self, url: str) -> list[dict]:
        cached = self._cache.get(url)
        if cached and (time.time() - cached.fetched_at) < self.cache_ttl_s:
            return cached.entries

        try:
            parsed = feedparser.parse(url)
            entries = [
                {"title": e.get("title", ""), "summary": e.get("summary", "")}
                for e in parsed.entries
            ]
        except Exception:
            logger.exception("Failed to fetch/parse news feed %s", url)
            entries = cached.entries if cached else []

        self._cache[url] = _CachedFeed(fetched_at=time.time(), entries=entries)
        return entries

    def headlines_for(self, symbol: str, company_name: str | None = None, limit: int = 5) -> list[str]:
        """Returns recent headline titles whose title or summary mentions
        `symbol` or `company_name` (case-insensitive substring match).
        """
        needles = [symbol.lower()]
        if company_name:
            needles.append(company_name.lower())

        matches: list[str] = []
        for url in self.feed_urls:
            for entry in self._fetch_feed(url):
                haystack = f"{entry['title']} {entry['summary']}".lower()
                if any(needle in haystack for needle in needles):
                    matches.append(entry["title"])
                if len(matches) >= limit:
                    return matches
        return matches
