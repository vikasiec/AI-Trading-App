"""Jev AI (TypeSafe System One) conviction scoring client.

Jev answers typed Choice / Score / Noul questions against a state you
supply -- it does not generate free text and never sees account state
or places orders. Treat its score as calibrated on the *question asked*,
not as a probability of profit; that mapping only exists once you've
measured logged scores against real trade outcomes (see audit.py).

Endpoint and payload shape confirmed against TypeSafe's public API
reference (POST https://api.typesafe.ai/v1/systemone, body: model,
state, questions; response: model, answers, usage) -- re-verify before
relying on this if TypeSafe's API has changed since.
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
    noise_score: float = 0.0  # V10: how noisy / unreliable the tape+news look


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
            "model": self.cfg.model,
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
                },
                "noise": {
                    "type": "score",
                    "instructions": (
                        "Rate how noisy or unreliable this state is "
                        "(auction chop, conflicting headlines, missing data). "
                        "High score means do not trust a long."
                    ),
                    "criteria": [
                        "Clean tape",
                        "Mild noise",
                        "Noisy",
                        "Untradeable noise",
                    ],
                },
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
        answers = body.get("answers") or {}
        answer = answers["conviction"]
        noise = answers.get("noise") or {}
        return ConvictionResult(
            score=answer["score"],
            confidence=answer["confidence"],
            raw=body,
            noise_score=float(noise.get("score") or 0.0),
        )

    def passes_threshold(self, result: ConvictionResult) -> bool:
        return (
            result.score >= self.cfg.conviction_threshold
            and result.confidence >= self.cfg.confidence_threshold
        )
