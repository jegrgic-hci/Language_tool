"""
Content bank: reusable, tagged French phrases and passages + their Chirp audio.

The PHRASE is the atomic unit — one sentence whose audio is a single Chirp mp3,
content-addressed and stored via ``library_store`` (synthesized once, ever). A
PASSAGE is a light record referencing an ordered list of phrase ids; all of a
passage's phrases share one narrator voice, so stitched playback sounds like one
speaker. Generating a passage therefore *seeds* the phrase pool that phrase,
shadow, and dictation exercises draw from.

Records are JSON on the shared store (Cloudflare R2 when configured, with a local
mirror; local disk otherwise), via ``library_store.object_get/object_put``, so the
bank is shared across local dev and production and compounds over time. Per-user
novelty (which units a learner has already seen) lives in the analytics SQLite DB,
not here.

Bucketing: pieces are indexed by (kind, register, level, topic) so a surface can
cheaply pull an unseen piece for the requested bucket.

Python 3.9 compatible (typing.Optional/List, no PEP 604 unions).
"""

import json
import re
import uuid
import random
import unicodedata
from datetime import datetime
from typing import Optional, List, Dict

import library_store

# Registers: "standard" (clean, STT-safe — shadow/paragraph/dictation/listen) and
# "casual" (Dialogue French). Kinds: "phrase" (one sentence) / "passage".
_BANK_PREFIX = "bank/"

# ── Reuse-vs-generate policy knobs (tune here) ────────────────────────────────────
POOL_TARGET = 20            # a bucket this deep is "mature" → eligible for recycle
POOL_MAX = 60               # stop growing a bucket past this (even via drip)
DRIP_RATE = 0.08            # chance to generate fresh even when unseen pieces exist
RECYCLE_MIN_AGE_DAYS = 30   # only recycle a piece the learner last saw ≥ this long ago


def _slug(text: str) -> str:
    """Filesystem/key-safe slug for a topic label (accent-stripped, lowercased)."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "misc"


# ── Topic canonicalization (so buckets actually collide) ──────────────────────────
# Reuse only kicks in when the same topic maps to the same bucket. We normalize the
# topic string (case/accents/spacing) and snap it to a registered canonical topic
# when it matches; otherwise it buckets by its normalized form. (Semantic matching
# of different wordings is a future upgrade.)
_CANON_LOOKUP: Dict[str, str] = {}   # normalized key -> canonical original string


def _normkey(topic: str) -> str:
    t = unicodedata.normalize("NFKD", topic or "").encode("ascii", "ignore").decode("ascii")
    t = re.sub(r"[^a-zA-Z0-9]+", " ", t).strip().lower()
    return re.sub(r"\s+", " ", t)


def register_canonical_topics(topics) -> None:
    """Register the app's canonical topic list (called once at server startup)."""
    global _CANON_LOOKUP
    _CANON_LOOKUP = {_normkey(t): t for t in (topics or []) if _normkey(t)}


def _canon_topic(topic: str) -> str:
    key = _normkey(topic)
    if not key:
        return "misc"
    return _CANON_LOOKUP.get(key, key)


def _record_key(kind: str, unit_id: str) -> str:
    sub = "passages" if kind == "passage" else "phrases"
    return "{}{}/{}.json".format(_BANK_PREFIX, sub, unit_id)


def _index_key(kind: str, register: str, level: str, topic: str, style: str) -> str:
    # Canonicalize the topic so case/accent/spacing variants share one bucket.
    return "{}index/{}/{}/{}/{}/{}.json".format(
        _BANK_PREFIX, kind, register, (level or "any").lower(),
        _slug(_canon_topic(topic)), _slug(style or "any")
    )


# ── Record + index I/O ────────────────────────────────────────────────────────────
def _get_json(key: str) -> Optional[dict]:
    raw = library_store.object_get(key)
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, AttributeError):
        return None


def _put_json(key: str, obj) -> None:
    library_store.object_put(key, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json")


def _load_index(kind: str, register: str, level: str, topic: str, style: str) -> List[str]:
    obj = _get_json(_index_key(kind, register, level, topic, style))
    return obj.get("ids", []) if isinstance(obj, dict) else []


def _append_index(kind: str, register: str, level: str, topic: str, style: str, unit_id: str) -> None:
    key = _index_key(kind, register, level, topic, style)
    obj = _get_json(key) or {"ids": []}
    ids = obj.get("ids", [])
    if unit_id not in ids:
        ids.append(unit_id)
    obj["ids"] = ids
    _put_json(key, obj)


# ── Public API ──────────────────────────────────────────────────────────────────
def add_phrase(text: str, register: str, level: str, topic: str, voice: str, audio_hash: str,
               style: str = "", noun_adj_tokens: Optional[list] = None) -> dict:
    """Bank a single phrase (its audio already synthesized to ``audio_hash``)."""
    topic = _canon_topic(topic)
    rec = {
        "id": uuid.uuid4().hex,
        "kind": "phrase",
        "text": text.strip(),
        "register": register,
        "level": level,
        "topic": topic,
        "style": style,
        "voice": voice,
        "audio_hash": audio_hash,
        "noun_adj_tokens": noun_adj_tokens or [],
    }
    # Phrases are pooled style-agnostically (style="") so a paragraph of any style
    # seeds one shared phrase pool per (register, level, topic) that phrase, shadow,
    # and dictation exercises all draw from (cross-pollination). Style is retained on
    # the record for reference. Passages keep style so a paragraph stays coherent.
    _put_json(_record_key("phrase", rec["id"]), rec)
    _append_index("phrase", register, level, topic, "", rec["id"])
    return rec


def add_passage(register: str, level: str, topic: str, voice: str, phrase_ids: List[str],
                style: str = "", noun_adj_tokens: Optional[list] = None,
                questions: Optional[list] = None, vocab_preview: Optional[list] = None,
                payload: Optional[dict] = None) -> dict:
    """Bank a passage. Phrase-atomic passages (paragraph shadowing) reference
    ``phrase_ids``; whole-text passages (Listen & Answer, Dialogue French) instead
    carry their text/structure in ``payload`` (e.g. {"text": ...} or
    {"title", "lines", "voices"}) and leave ``phrase_ids`` empty — audio reuse then
    comes from the content-addressed cache when the same (voice, text) is requested."""
    topic = _canon_topic(topic)
    rec = {
        "id": uuid.uuid4().hex,
        "kind": "passage",
        "register": register,
        "level": level,
        "topic": topic,
        "style": style,
        "voice": voice,
        "phrase_ids": phrase_ids,
        "noun_adj_tokens": noun_adj_tokens or [],
        "questions": questions or [],
        "vocab_preview": vocab_preview or [],
    }
    if payload:
        rec.update(payload)
    _put_json(_record_key("passage", rec["id"]), rec)
    _append_index("passage", register, level, topic, style, rec["id"])
    return rec


def get_phrase(unit_id: str) -> Optional[dict]:
    return _get_json(_record_key("phrase", unit_id))


def get_passage(unit_id: str) -> Optional[dict]:
    return _get_json(_record_key("passage", unit_id))


def passage_phrases(passage: dict) -> List[dict]:
    """Hydrate a passage's phrase records, in order (skips any that went missing)."""
    out = []
    for pid in passage.get("phrase_ids", []):
        rec = get_phrase(pid)
        if rec:
            out.append(rec)
    return out


def attach_questions(passage_id: str, questions: list, vocab_preview: Optional[list] = None) -> Optional[dict]:
    """Add a comprehension layer to a banked passage the first time it's used for
    Listen & Answer (text-only; no new audio)."""
    rec = get_passage(passage_id)
    if not rec:
        return None
    rec["questions"] = questions or []
    if vocab_preview is not None:
        rec["vocab_preview"] = vocab_preview
    _put_json(_record_key("passage", passage_id), rec)
    return rec


def bucket_ids(kind: str, register: str, level: str, topic: str, style: str = "") -> List[str]:
    return _load_index(kind, register, level, topic, style)


def count(kind: str, register: str, level: str, topic: str, style: str = "") -> int:
    return len(_load_index(kind, register, level, topic, style))


# ── Pool inventory (admin) ────────────────────────────────────────────────────────
# Content type is fully recoverable from a bucket's index-key path
# (bank/index/{kind}/{register}/{level}/{topic}/{style}.json), so we can size the
# whole banked pool by walking index files only — no need to load every record.
def _category(kind: str, register: str) -> str:
    if kind == "phrase":
        return "phrases"
    if register == "listen":
        return "listen_answer"   # Listen & Answer passages
    if register == "casual":
        return "dialogue"        # Dialogue French passages
    if register == "standard":
        return "paragraphs"      # paragraph-shadowing passages
    return "other_passages"


def bank_stats() -> dict:
    """Size the banked content pool by content type. Counts are distinct banked
    units per bucket (each phrase/passage's Chirp3-HD audio is synthesized once and
    cached forever). Aggregated from the per-bucket index files, plus a bucket-level
    breakdown for detail."""
    index_prefix = "{}index/".format(_BANK_PREFIX)
    totals = {"phrases": 0, "paragraphs": 0, "listen_answer": 0,
              "dialogue": 0, "other_passages": 0}
    buckets = []
    for key in library_store.list_keys(index_prefix):
        if not key.endswith(".json"):
            continue
        obj = _get_json(key)
        if not isinstance(obj, dict):
            continue
        n = len(obj.get("ids", []))
        if n == 0:
            continue
        parts = key[len(index_prefix):].split("/")
        if len(parts) < 5:
            continue
        kind, register, level, topic = parts[0], parts[1], parts[2], parts[3]
        style = parts[4][:-5] if parts[4].endswith(".json") else parts[4]
        cat = _category(kind, register)
        totals[cat] += n
        buckets.append({
            "category": cat, "kind": kind, "register": register,
            "level": level, "topic": topic, "style": style, "count": n,
        })
    buckets.sort(key=lambda b: (b["category"], -b["count"], b["level"]))
    return {
        "totals": totals,
        "total_units": sum(totals.values()),
        "bucket_count": len(buckets),
        "buckets": buckets,
        "storage": library_store.storage_backend(),
        "chirp_enabled": library_store.chirp_enabled(),
        "chirp_chars_used_month": library_store.chars_used_this_month(),
        "chirp_char_cap": library_store.monthly_char_cap(),
        "chirp_free_tier": library_store.FREE_TIER_CHARS,
        "ops": library_store.ops_counters(),
    }


def _load(kind: str, unit_id: str) -> Optional[dict]:
    return get_passage(unit_id) if kind == "passage" else get_phrase(unit_id)


def pick_unseen(kind: str, register: str, level: str, topic: str, style: str = "",
                seen_ids: Optional[set] = None) -> Optional[dict]:
    """A random banked unit for the bucket that the user hasn't seen, or None when
    the bucket is empty or exhausted (caller then generates + banks a new one)."""
    seen = seen_ids or set()
    ids = [i for i in _load_index(kind, register, level, topic, style) if i not in seen]
    if not ids:
        return None
    random.shuffle(ids)
    for uid in ids:
        rec = _load(kind, uid)
        if rec:
            return rec
    return None


def _age_days(ts: str) -> float:
    """Days since a SQLite `datetime('now')` (naive UTC) timestamp. Unknown → very old."""
    try:
        return (datetime.utcnow() - datetime.fromisoformat(ts.replace(" ", "T"))).days
    except Exception:
        return 1e9


def select_for_user(kind: str, register: str, level: str, topic: str, style: str,
                    seen_map: Optional[dict], budget_ok: bool) -> Optional[dict]:
    """The reuse-vs-generate decision for one (learner, bucket).

    Returns a banked record to reuse, or None meaning "generate + bank a new one".
    Policy:
      • Unseen pieces exist → serve one (free), except a small `DRIP_RATE` chance to
        generate fresh (only while under the soft budget and below `POOL_MAX`) so
        mature buckets keep evolving.
      • Learner has seen the whole bucket → **spaced recycle**: replay their
        least-recently-seen piece only if the bucket is deep (`≥ POOL_TARGET`) AND
        they last saw that piece ≥ `RECYCLE_MIN_AGE_DAYS` ago; otherwise generate
        (budget permitting). If the budget is tight, recycle rather than spend.
    """
    seen_map = seen_map or {}
    ids = _load_index(kind, register, level, topic, style)
    depth = len(ids)
    if not ids:
        return None  # empty bucket → generate the first piece

    unseen = [i for i in ids if i not in seen_map]
    if unseen:
        if budget_ok and depth < POOL_MAX and random.random() < DRIP_RATE:
            return None  # freshness drip
        random.shuffle(unseen)
        for uid in unseen:
            rec = _load(kind, uid)
            if rec:
                return rec

    # Learner has seen everything in the bucket (or unseen records went missing).
    seen_here = [(i, seen_map[i]) for i in ids if i in seen_map]
    if seen_here:
        oldest_id, oldest_ts = min(seen_here, key=lambda x: x[1])
        if depth >= POOL_TARGET and _age_days(oldest_ts) >= RECYCLE_MIN_AGE_DAYS:
            return _load(kind, oldest_id)   # spaced recycle (free)
        if budget_ok:
            return None                     # generate fresh
        return _load(kind, oldest_id)       # budget tight → recycle anyway
    return None
