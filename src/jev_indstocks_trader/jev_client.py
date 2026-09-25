"""Jev AI (TypeSafe System One) conviction scoring client.

Jev answers typed Choice / Score / Noul questions against a state you
supply -- it does not generate free text and never sees account state
or places orders. Treat its score as calibrated on the *question asked*,
not as a probability of profit; that mapping only exists once you've
measured logged scores against real trade outcomes (see audit.py).

NOTE: verify the endpoint path and exact response schema against Jev's
current docs before relying on this in production -- confirmed details
below are from public documentation as of Sept 2026; the API is new and
may change.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

from .config import JevConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConvictionResult:
    score: float          # probability-weighted level index (see Jev docs for `Score` semantics)
    confidence: float     # 0-1, calibrated confidence in this specific answer
    raw: dict              # full response payload, kept for the audit log


class JevEvaluator:
    def __init__(self, cfg: JevConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()

    def evaluate_signal(self, market_context: str) -> ConvictionResult:
        """Score conviction that `market_context` describes a high-probability long entry.

        `market_context` should already be compressed to a fixed token
        budget upstream (see feature_prep.py) -- Jev is a scorer, not a
        summarizer.
        """
        payload = {
            "state": market_context,
            "questions": {
                "conviction": {
                    "type": "score",
                    "instructions": (
                        "Rate conviction that this setup is a high-probability "
                        "long entry, based only on the state provided."
                    ),
                    "criteria": [
                        "No edge",
                        "Weak edge",
                        "Moderate edge",
                        "Strong edge",
                    ],
                }
            },
        }
        resp = self.session.post(
            self.cfg.base_url,
            headers={"Authorization": f"Bearer {self.cfg.api_key}"},
            json=payload,
            timeout=self.cfg.request_timeout_s,
        )
        resp.raise_for_status()
        body = resp.json()
        answer = body["answers"]["conviction"]
        return ConvictionResult(score=answer["score"], confidence=answer["confidence"], raw=body)

    def passes_threshold(self, result: ConvictionResult) -> bool:
        return (
            result.score >= self.cfg.conviction_threshold
            and result.confidence >= self.cfg.confidence_threshold
        )
