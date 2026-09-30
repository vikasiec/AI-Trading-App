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

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests

from .config import JevConfig

logger = logging.getLogger(__name__)

_SECRET_KEYS = re.compile(
    r"(key|token|secret|auth|password|credential|account)",
    re.IGNORECASE,
)

_DEFAULT_RAW_SAMPLE_PATH = Path.home() / ".indstocks" / "jev_raw_sample.json"


def _strip_secrets(obj: object) -> object:
    """Recursively remove dict keys that look like credentials."""
    if isinstance(obj, dict):
        return {
            k: _strip_secrets(v)
            for k, v in obj.items()
            if not _SECRET_KEYS.search(k)
        }
    if isinstance(obj, list):
        return [_strip_secrets(item) for item in obj]
    return obj


@dataclass(frozen=True)
class ConvictionResult:
    score: float          # probability-weighted level index (see Jev docs for `Score` semantics)
    confidence: float     # 0-1, calibrated confidence in this specific answer
    raw: dict              # full response payload, kept for the audit log
    noise_score: float = 0.0  # V10: how noisy / unreliable the tape+news look


class JevEvaluator:
    def __init__(
        self,
        cfg: JevConfig,
        session: requests.Session | None = None,
        raw_sample_path: Path | None = None,
    ):
        self.cfg = cfg
        self.session = session or requests.Session()
        self._raw_sample_path = raw_sample_path or _DEFAULT_RAW_SAMPLE_PATH
        self._raw_dumped = self._raw_sample_path.exists()

    def _dump_raw_sample(self, payload: dict, body: dict) -> None:
        """Write one raw request+response to disk, stripping credentials."""
        if self._raw_dumped:
            return
        try:
            sample = _strip_secrets({"request": payload, "response": body})
            self._raw_sample_path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(
                dir=str(self._raw_sample_path.parent), suffix=".tmp"
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(sample, f, indent=2, default=str)
                os.replace(tmp, str(self._raw_sample_path))
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            self._raw_dumped = True
            logger.info("Jev raw sample written to %s", self._raw_sample_path)
        except Exception:
            logger.warning("Failed to write Jev raw sample", exc_info=True)

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
        self._dump_raw_sample(payload, body)
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
