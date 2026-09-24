"""
Azure Pronunciation Assessment — shared helpers.

The browser runs Azure's Speech SDK against a known reference text and gets back a
phoneme-level grade of what was actually *said*, which browser STT structurally
can't give (Chrome autocorrects "tink" to "think" from context). This module mints
the short-lived browser token and turns Azure's result JSON into per-word tiers.

Free tier (F0) resources never bill: once the monthly allowance is used, Azure
cancels requests and callers fall back to Web Speech scoring.

Python 3.9 compatible (typing.Optional, no PEP 604 unions).
"""

import json
import os
from typing import Optional

import httpx

AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "")

# Per-word 3-tier grading: green (solid) / amber (acceptable, minor polish) / red
# (needs work). Pass = overall PronScore >= PASS_SCORE and no red/omitted word.
PASS_SCORE = 80
WORD_GREEN = 80
WORD_AMBER = 60


def configured() -> bool:
    return bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)


async def mint_token() -> Optional[dict]:
    """A ~10-minute browser token ({token, region}) so the key never leaves the
    server, or None when Azure isn't configured or refuses."""
    if not configured():
        return None
    url = "https://{}.api.cognitive.microsoft.com/sts/v1.0/issueToken".format(AZURE_SPEECH_REGION)
    async with httpx.AsyncClient() as client:
        r = await client.post(url, headers={"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY,
                                            "Content-Length": "0"}, timeout=10)
    if r.status_code != 200:
        return None
    return {"token": r.text, "region": AZURE_SPEECH_REGION}


def word_tier(accuracy: float, error: str) -> str:
    if error == "Omission":
        return "omitted"
    if error == "Insertion":
        return "red"
    # None / Mispronunciation: tier by accuracy, so decent-but-imperfect isn't red.
    if accuracy >= WORD_GREEN:
        return "green"
    if accuracy >= WORD_AMBER:
        return "amber"
    return "red"


def parse(raw: str) -> dict:
    """Azure result JSON -> {score (0-1), passed, text, words}. Each word is
    {word, accuracy, error, tier, weakest: (phoneme, score) or None}. Inserted words
    (said but not in the reference) are kept, flagged by error == "Insertion"."""
    data = json.loads(raw)
    nbest = data.get("NBest") or []
    if not nbest:
        return {"score": 0.0, "passed": False, "text": "", "words": []}
    nb = nbest[0]
    pa = nb.get("PronunciationAssessment") or {}
    pron = pa.get("PronScore", pa.get("AccuracyScore", 0)) or 0
    words = []
    for w in nb.get("Words") or []:
        wa = w.get("PronunciationAssessment") or {}
        acc = wa.get("AccuracyScore", 0) or 0
        err = wa.get("ErrorType", "None") or "None"
        weakest = None
        for p in w.get("Phonemes") or []:
            pacc = (p.get("PronunciationAssessment") or {}).get("AccuracyScore", 100)
            if weakest is None or pacc < weakest[1]:
                weakest = (p.get("Phoneme", ""), pacc)
        words.append({"word": w.get("Word", ""), "accuracy": acc, "error": err,
                      "tier": word_tier(acc, err), "weakest": weakest})
    has_problem = any(w["tier"] in ("red", "omitted") for w in words)
    return {
        "score": round(pron / 100.0, 3),
        "passed": pron >= PASS_SCORE and not has_problem,
        "text": nb.get("Display") or data.get("DisplayText") or "",
        "words": words,
    }
