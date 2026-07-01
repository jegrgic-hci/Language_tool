"""
Library storage + Chirp3-HD French TTS with content-addressed caching.

Audio is content-addressed: the object key is md5(voice|text) + ".mp3", so an
identical (voice, text) pair is synthesized exactly ONCE, ever, and reused for
free forever. Chirp3-HD (Google Cloud TTS) is the voice engine; callers fall
back to edge-tts when Chirp is not configured.

The storage backend is chosen at import time from the environment:
  * Cloudflare R2 (S3-compatible) when the R2_* vars are set  -> one shared
    library across local dev and Render production (no double-billing, no sync).
  * Local disk (DATA_DIR/library/audio) otherwise             -> dev fallback.

In R2 mode the local dir still acts as a read-through cache so a warm instance
never re-fetches. Nothing on the way out is vendor-specific: audio is plain MP3
and passage metadata is plain JSON, so the library stays fully portable.

Python 3.9 compatible (typing.Optional, no PEP 604 unions).
"""

import os
import json
import base64
import hashlib
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── Config (read once at import) ────────────────────────────────────────────────
_GOOGLE_KEY = os.environ.get("GOOGLE_TTS_API_KEY")
_TTS_ENDPOINT = "https://texttospeech.googleapis.com/v1/text:synthesize?key={}"

_DATA_DIR = Path(os.environ.get("DATA_DIR", Path(__file__).parent / "data"))
_LOCAL_AUDIO_DIR = _DATA_DIR / "library" / "audio"
_LOCAL_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

_R2_ENDPOINT = os.environ.get("R2_ENDPOINT")
_R2_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
_R2_SECRET = os.environ.get("R2_SECRET_ACCESS_KEY")
_R2_BUCKET = os.environ.get("R2_BUCKET")
_USE_R2 = all([_R2_ENDPOINT, _R2_KEY_ID, _R2_SECRET, _R2_BUCKET])

_AUDIO_PREFIX = "audio/"  # object key prefix inside the bucket

# Monthly Chirp free-tier budget guard. Chirp3-HD bills per character with a
# 1M-chars/month free tier; we stop *new* synthesis at a soft cap below that and
# let callers fall back to edge-tts. Cache hits never count and always serve.
_MONTHLY_CHAR_CAP = 900_000
_USAGE_KEY = "usage/chirp_chars.json"     # object key in the shared store


def chirp_enabled() -> bool:
    """True when Chirp3-HD synthesis is available (Google key present)."""
    return bool(_GOOGLE_KEY)


def storage_backend() -> str:
    return "r2" if _USE_R2 else "local"


# ── R2 (S3-compatible) client — lazy so boto3 is only needed when configured ─────
_s3 = None


def _client():
    global _s3
    if _s3 is None:
        import boto3  # deferred import; only required in R2 mode
        _s3 = boto3.client(
            "s3",
            endpoint_url=_R2_ENDPOINT,
            aws_access_key_id=_R2_KEY_ID,
            aws_secret_access_key=_R2_SECRET,
            region_name="auto",
        )
    return _s3


# ── Content addressing ──────────────────────────────────────────────────────────
def audio_key(text: str, voice: str) -> str:
    """Deterministic filename for a (voice, text) pair. 32 hex chars + .mp3, which
    also satisfies the server's /audio filename validation regex."""
    digest = hashlib.md5("{}|{}".format(voice, text.strip()).encode("utf-8")).hexdigest()
    return "{}.mp3".format(digest)


# ── Google Chirp3-HD synthesis ──────────────────────────────────────────────────
def _synth_chirp(text: str, voice: str) -> bytes:
    body = json.dumps({
        "input": {"text": text},
        "voice": {"languageCode": "fr-FR", "name": voice},
        "audioConfig": {"audioEncoding": "MP3"},
    }).encode("utf-8")
    req = urllib.request.Request(
        _TTS_ENDPOINT.format(_GOOGLE_KEY),
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return base64.b64decode(payload["audioContent"])


# ── Backend I/O (local read-through cache + optional R2) ─────────────────────────
def _local_path(key: str) -> Path:
    return _LOCAL_AUDIO_DIR / key


def _write_local(key: str, data: bytes) -> None:
    _local_path(key).write_bytes(data)


def _r2_exists(key: str) -> bool:
    from botocore.exceptions import ClientError
    try:
        _client().head_object(Bucket=_R2_BUCKET, Key=_AUDIO_PREFIX + key)
        return True
    except ClientError:
        return False


def _r2_get(key: str) -> Optional[bytes]:
    from botocore.exceptions import ClientError
    try:
        obj = _client().get_object(Bucket=_R2_BUCKET, Key=_AUDIO_PREFIX + key)
        return obj["Body"].read()
    except ClientError:
        return None


def _r2_put(key: str, data: bytes) -> None:
    _client().put_object(
        Bucket=_R2_BUCKET, Key=_AUDIO_PREFIX + key, Body=data, ContentType="audio/mpeg"
    )


def _store(key: str, data: bytes) -> None:
    """Persist to the shared library (R2 when configured) and the local cache."""
    _write_local(key, data)
    if _USE_R2:
        _r2_put(key, data)


# ── Generic object I/O (arbitrary keys, no audio prefix) ─────────────────────────
# Used by the usage counter and the content bank (JSON metadata). Writes to both
# the shared store (R2 when configured) and a local mirror under DATA_DIR.
def object_get(key: str) -> Optional[bytes]:
    """Fetch an arbitrary object by full key. R2 is source of truth when
    configured (bank JSON mutates across instances); local disk otherwise."""
    if _USE_R2:
        from botocore.exceptions import ClientError
        try:
            obj = _client().get_object(Bucket=_R2_BUCKET, Key=key)
            return obj["Body"].read()
        except ClientError:
            return None
    p = _DATA_DIR / key
    return p.read_bytes() if p.exists() else None


def object_put(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    """Persist an arbitrary object to the shared store (R2) and a local mirror."""
    p = _DATA_DIR / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    if _USE_R2:
        _client().put_object(Bucket=_R2_BUCKET, Key=key, Body=data, ContentType=content_type)


def list_keys(prefix: str):
    """List every object key under ``prefix`` in the active backend (R2 when
    configured, else the local mirror). Returns full keys (no ``audio/`` munging),
    matching what ``object_get`` expects. R2 listing is paginated over all pages."""
    if _USE_R2:
        keys = []
        token = None
        while True:
            kwargs = {"Bucket": _R2_BUCKET, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = _client().list_objects_v2(**kwargs)
            keys.extend(obj["Key"] for obj in resp.get("Contents", []))
            if resp.get("IsTruncated"):
                token = resp.get("NextContinuationToken")
            else:
                break
        return keys
    root = _DATA_DIR / prefix
    if not root.exists():
        return []
    return [str(p.relative_to(_DATA_DIR)) for p in root.rglob("*") if p.is_file()]


# ── Monthly budget guard ─────────────────────────────────────────────────────────
def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _read_usage() -> dict:
    """Current usage record {month, chars}, from the shared store (R2) when
    configured, else local. A record from a past month reads as zero for the
    current month (monthly reset)."""
    raw = object_get(_USAGE_KEY)
    month = _current_month()
    if raw:
        try:
            rec = json.loads(raw.decode("utf-8"))
            if rec.get("month") == month:
                return {"month": month, "chars": int(rec.get("chars", 0))}
        except (ValueError, AttributeError):
            pass
    return {"month": month, "chars": 0}


def _write_usage(rec: dict) -> None:
    object_put(_USAGE_KEY, json.dumps(rec).encode("utf-8"), "application/json")


def chars_used_this_month() -> int:
    """Chirp characters synthesized (billably) in the current calendar month."""
    return _read_usage()["chars"]


def monthly_char_cap() -> int:
    """The soft cap where *new* Chirp synthesis stops (below the 1M free tier)."""
    return _MONTHLY_CHAR_CAP


FREE_TIER_CHARS = 1_000_000  # Chirp3-HD free characters per calendar month


# ── Ops counters (monthly, shared) ───────────────────────────────────────────────
# Lightweight operational tallies for the admin view: how often content was served
# from the bank (hit) vs. generated fresh (miss), and how often Chirp synthesis fell
# back to edge-tts. Monthly, reset like the char usage. Increments are best-effort
# (non-atomic read-modify-write) — fine for an approximate ops metric.
_OPS_KEY = "usage/ops_counters.json"
_OPS_FIELDS = ("bank_hits", "bank_misses", "edge_fallbacks")


def _read_ops() -> dict:
    raw = object_get(_OPS_KEY)
    month = _current_month()
    rec = {"month": month, "bank_hits": 0, "bank_misses": 0, "edge_fallbacks": 0}
    if raw:
        try:
            saved = json.loads(raw.decode("utf-8"))
            if saved.get("month") == month:
                for f in _OPS_FIELDS:
                    rec[f] = int(saved.get(f, 0))
        except (ValueError, AttributeError):
            pass
    return rec


def _bump_ops(field: str, n: int = 1) -> None:
    if field not in _OPS_FIELDS:
        return
    rec = _read_ops()
    rec[field] += n
    _write_usage_obj(_OPS_KEY, rec)


def _write_usage_obj(key: str, rec: dict) -> None:
    object_put(key, json.dumps(rec).encode("utf-8"), "application/json")


def record_bank_hit() -> None:
    _bump_ops("bank_hits")


def record_bank_miss() -> None:
    _bump_ops("bank_misses")


def record_edge_fallback() -> None:
    _bump_ops("edge_fallbacks")


def ops_counters() -> dict:
    """Current-month bank hit/miss + edge-tts fallback tallies (shared store)."""
    return _read_ops()


def _budget_available() -> bool:
    return chars_used_this_month() < _MONTHLY_CHAR_CAP


# Soft ceiling for *discretionary* generation (growing/refreshing the bank). Below
# this, the selection policy may generate new content; above it, it serves/recycles
# only — leaving headroom under the hard cap for genuinely novel requests.
_SOFT_FRAC = 0.7


def generation_budget_ok() -> bool:
    """True when there's budget headroom to generate *new* bank content (vs reuse)."""
    return chars_used_this_month() < int(_MONTHLY_CHAR_CAP * _SOFT_FRAC)


def _record_usage(chars: int) -> None:
    rec = _read_usage()
    rec["chars"] += chars
    _write_usage(rec)


# ── Public API ──────────────────────────────────────────────────────────────────
def synth_and_cache(text: str, voice: str) -> str:
    """Return the audio object key, synthesizing + storing only on a cache miss.

    Order: local read-through cache -> shared R2 library -> Google (bills once).
    Raises when Chirp is unavailable or the API call fails, so the caller can
    fall back to edge-tts.
    """
    if not _GOOGLE_KEY:
        raise RuntimeError("GOOGLE_TTS_API_KEY not set")
    text = text.strip()
    key = audio_key(text, voice)

    if _local_path(key).exists():
        return key
    if _USE_R2 and _r2_exists(key):
        return key  # in the shared library; /audio fetches + caches on demand

    # Cache miss → this would bill. Stop new synthesis at the monthly soft cap so
    # the caller falls back to edge-tts; anything already banked still serves.
    if not _budget_available():
        raise RuntimeError(
            "Chirp monthly character budget reached ({} chars)".format(_MONTHLY_CHAR_CAP)
        )

    audio = _synth_chirp(text, voice)  # the one billable moment
    _store(key, audio)
    _record_usage(len(text))
    return key


def get_audio(key: str) -> Optional[bytes]:
    """Bytes for a cached library audio object, or None if unknown.

    Serves the local cache first; in R2 mode, fetches from the shared library on
    a miss and writes it into the local cache for next time.
    """
    local = _local_path(key)
    if local.exists():
        return local.read_bytes()
    if _USE_R2:
        data = _r2_get(key)
        if data is not None:
            _write_local(key, data)
        return data
    return None
