"""Small retry/backoff helper.

Used to wrap individual network calls (an LTP fetch, a Jev call) rather
than blanket-decorating every method -- keeps call sites explicit about
which operations are safe to retry (idempotent reads) versus which
aren't (an order placement should NOT be blindly retried without an
idempotency key, since a retry could double-submit).
"""
from __future__ import annotations

import logging
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_with_backoff(
    func: Callable[[], T],
    max_retries: int = 3,
    base_delay_s: float = 1.0,
    retry_on: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Calls func() with exponential backoff on the given exception types.

    Raises the last exception if all retries are exhausted.
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries):
        try:
            return func()
        except retry_on as exc:
            last_exc = exc
            if attempt == max_retries - 1:
                break
            delay = base_delay_s * (2 ** attempt)
            logger.warning(
                "Retryable error on attempt %d/%d: %s -- backing off %.1fs",
                attempt + 1, max_retries, exc, delay,
            )
            time.sleep(delay)
    assert last_exc is not None
    raise last_exc
