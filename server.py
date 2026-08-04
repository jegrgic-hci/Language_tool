import os
import re
import uuid
import json
import random
import tempfile
from pathlib import Path
from typing import Optional
from datetime import date, timedelta

from fastapi import Depends, FastAPI, Header, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

# Must run before any local module is imported: auth.py (and others) read their
# config from os.environ at import time, so loading .env later leaves them on
# their fallbacks — for JWT_SECRET that means a fresh random secret on every
# reload, silently invalidating every issued token.
load_dotenv()

import asyncio
import logging
import edge_tts
from mistralai import Mistral
import httpx
import auth as _auth

BASE_DIR = Path(__file__).parent

from document_engine import UPLOADS_DIR
import shadow_engine as _shadow_module
from shadow_engine import generate_phrase, score_attempt, analyze_mismatches as analyze_shadow_mismatches
import paragraph_engine as _paragraph_module
from paragraph_engine import generate_paragraph, score_chunk, TOPICS, analyze_mismatches, analyze_patterns
from score_utils import normalize, run_sequence_match, build_display_results, analyze_dictation_mismatches
import practice_list as pl
import analytics as _analytics
import library_store
import content_bank
from pos_tagger import tag_nouns_adjs
from prosody_engine import annotate_phrase_rhythm

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

_api_key: str = os.environ.get("MISTRAL_API_KEY", "")
_mistral = Mistral(api_key=_api_key) if _api_key else None
_MODEL = "mistral-large-latest"

# Comma-separated list of valid access codes, e.g. "CODE1,CODE2,CODE3"
_ACCESS_CODES: set = {
    c.strip() for c in os.environ.get("ACCESS_CODES", "").split(",") if c.strip()
}

_ANALYTICS_KEYS: set = {
    k.strip() for k in os.environ.get("ANALYTICS_KEY", "").split(",") if k.strip()
}

_analytics.init_db()

_sa_email    = os.environ.get("SUPER_ADMIN_EMAIL", "")
_sa_password = os.environ.get("SUPER_ADMIN_PASSWORD", "")
if _sa_email and _sa_password:
    _analytics.seed_super_admin(_sa_email, _auth.hash_password(_sa_password))
else:
    import logging
    logging.getLogger("auth").warning(
        "SUPER_ADMIN_EMAIL / SUPER_ADMIN_PASSWORD not set — no super admin created."
    )

AUDIO_DIR = Path(tempfile.gettempdir()) / "vraifrench_audio"
AUDIO_DIR.mkdir(exist_ok=True)

_SMTP2GO_API_KEY = os.environ.get("SMTP2GO_API_KEY", "")
_EMAIL_FROM      = os.environ.get("EMAIL_FROM", "VraiFrench <noreply@vraifrench.com>")
_APP_BASE_URL    = os.environ.get("APP_BASE_URL", "http://127.0.0.1:8000")


async def _send_email(to: str, subject: str, html: str) -> bool:
    """Send one transactional email via the SMTP2GO HTTP API. Returns True on success."""
    if not _SMTP2GO_API_KEY:
        return False
    async with httpx.AsyncClient() as client:
        r = await client.post(
            "https://api.smtp2go.com/v3/email/send",
            headers={"Content-Type": "application/json"},
            json={
                "api_key": _SMTP2GO_API_KEY,
                "sender": _EMAIL_FROM,
                "to": [to],
                "subject": subject,
                "html_body": html,
            },
            timeout=10,
        )
        return r.status_code == 200


from contextlib import contextmanager

@contextmanager
def _use_dataset(dataset: str):
    path = _analytics.LEGACY_DB_PATH if dataset == "legacy" else _analytics.DB_PATH
    token = _analytics._active_db.set(path)
    try:
        yield
    finally:
        _analytics._active_db.reset(token)


# ── Helpers ────────────────────────────────────────────────────────────────────

VOICE = "fr-FR-DeniseNeural"
VOICE_B = "fr-FR-HenriNeural"  # second speaker for natural dialogue mode

# Chirp3-HD voices (Google Cloud TTS) for the cached listening library. Used only
# by the listening modes (Listen & Answer + Natural French) where audio is banked
# and reused; everything else stays on free edge-tts.
_CHIRP_PREFIX = "fr-FR-Chirp3-HD-"
CHIRP_VOICES_F = [_CHIRP_PREFIX + n for n in ("Aoede", "Kore", "Leda", "Zephyr")]
CHIRP_VOICES_M = [_CHIRP_PREFIX + n for n in ("Puck", "Charon", "Fenrir", "Orus")]
CHIRP_VOICES = CHIRP_VOICES_F + CHIRP_VOICES_M
_CHIRP_RANDOM = "chirp-random"  # sentinel: /tts picks a random narrator voice
_CHIRP_DEFAULT = "chirp-default"  # sentinel: /tts uses the fixed default narrator
# Fixed narrator for stable-cache phrase/passage playback (context phrases, custom
# content, saved practice items). One voice keeps each text → one md5, so the cache
# saturates over a finite/recurring set of texts.
DEFAULT_CHIRP_VOICE = _CHIRP_PREFIX + "Charon"

# A natural, gender-matched French first name per voice, so Dialogue French speakers
# read like real people ("Salut Julien !") instead of the Chirp codenames.
CHIRP_VOICE_NAMES = {
    _CHIRP_PREFIX + "Aoede":  "Chloé",
    _CHIRP_PREFIX + "Kore":   "Léa",
    _CHIRP_PREFIX + "Leda":   "Manon",
    _CHIRP_PREFIX + "Zephyr": "Inès",
    _CHIRP_PREFIX + "Puck":   "Lucas",
    _CHIRP_PREFIX + "Charon": "Julien",
    _CHIRP_PREFIX + "Fenrir": "Thomas",
    _CHIRP_PREFIX + "Orus":   "Hugo",
}


def voice_display_name(voice: str) -> str:
    return CHIRP_VOICE_NAMES.get(voice, voice.rsplit("-", 1)[-1])


def pick_narrator_voice() -> str:
    """A random Chirp3-HD voice for a single-narrator passage (Listen & Answer)."""
    return random.choice(CHIRP_VOICES)


def pick_dialogue_voices() -> tuple:
    """A random (voice_A, voice_B) pair for Natural French. One male + one female,
    order randomized, so the two speakers are always easy to tell apart."""
    va, vb = random.choice(CHIRP_VOICES_M), random.choice(CHIRP_VOICES_F)
    return (va, vb) if random.random() < 0.5 else (vb, va)

_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001F9FF"   # symbols, pictographs, emoticons
    "\U00002702-\U000027B0"    # dingbats
    "\U000024C2-\U0001F251"    # enclosed characters
    "\U0001F1E0-\U0001F1FF"    # regional indicator / flags
    "]+",
    flags=re.UNICODE,
)

def clean_for_tts(text: str) -> str:
    text = _EMOJI_RE.sub("", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)   # **bold**
    text = re.sub(r"\*(.+?)\*",     r"\1", text)   # *italic*
    text = re.sub(r"_(.+?)_",       r"\1", text)   # _italic_
    text = re.sub(r"[•·▪▸►\-]{1,}\s*", " ", text)  # bullet points
    text = re.sub(r"—",  ", ",  text)               # em dash → pause
    text = re.sub(r"…",  "...", text)               # ellipsis
    text = re.sub(r"[#`~]", "", text)               # leftover markdown chars
    text = text.replace("‿", " ").replace("⁀", " ")  # link marks → space so TTS sees clean French
    text = re.sub(r"\s{2,}", " ", text)             # collapse whitespace
    return text.strip()


async def generate_audio(text: str, voice: str = VOICE, rate: str = "+0%") -> str:
    filename = f"{uuid.uuid4().hex}.mp3"
    await edge_tts.Communicate(clean_for_tts(text), voice, rate=rate).save(str(AUDIO_DIR / filename))
    return filename


async def generate_library_audio(text: str, chirp_voice: str, edge_voice: str = VOICE) -> str:
    """Audio for the listening library: Chirp3-HD with content-addressed caching
    (synthesized once per unique text, then reused for free), falling back to
    edge-tts if Chirp isn't configured or the call fails. Returns an /audio filename.

    The returned filename is always a 32-hex `.mp3` (md5 for Chirp, uuid for edge),
    so it passes the /audio route's validation and is served from either the temp
    dir (edge) or the shared library (Chirp) transparently.
    """
    cleaned = clean_for_tts(text)
    if library_store.chirp_enabled() and chirp_voice.startswith(_CHIRP_PREFIX):
        try:
            return await asyncio.to_thread(library_store.synth_and_cache, cleaned, chirp_voice)
        except Exception as e:
            logging.getLogger("tts").warning("Chirp3 synth failed, falling back to edge-tts: %s", e)
            library_store.record_edge_fallback()
    return await generate_audio(cleaned, edge_voice)


# ── Content bank helpers ─────────────────────────────────────────────────────────

# The bank's canonical topic list — so case/accent/spacing variants of the same
# topic share one bucket (reuse only works when buckets collide).
content_bank.register_canonical_topics(list(TOPICS))


def _bank_pick(kind: str, register: str, level: str, topic: str, style: str,
               access_code: Optional[str]) -> Optional[dict]:
    """Apply the reuse-vs-generate policy for a (learner, bucket): returns a banked
    record to reuse, or None meaning the caller should generate + bank a new one."""
    budget_ok = library_store.generation_budget_ok()
    # No access_code = local/personal use (only the maintainer, testing). The per-user
    # seen-map is empty, so the normal policy would serve the same shallow-bucket piece
    # forever. Instead prefer generating fresh to grow the bank toward POOL_MAX while
    # budget allows; only reuse once the bucket is full or the budget is tight.
    if not access_code:
        if budget_ok and content_bank.count(kind, register, level, topic, style) < content_bank.POOL_MAX:
            rec = None
        else:
            rec = content_bank.pick_unseen(kind, register, level, topic, style)
    else:
        seen_map = _analytics.get_bank_seen_map(access_code)
        rec = content_bank.select_for_user(kind, register, level, topic, style, seen_map, budget_ok)
    # Efficiency telemetry: a returned record = served from the bank (no Mistral/Chirp
    # spend); None = the caller will generate + bank a fresh unit (a billable miss).
    if rec is not None:
        library_store.record_bank_hit()
    else:
        library_store.record_bank_miss()
    return rec


async def _synth_and_bank_phrase(text: str, register: str, level: str, topic: str,
                                 style: str, voice: str) -> dict:
    """Synthesize a phrase with Chirp (content-addressed), tag it, and bank it as a
    reusable PHRASE. Returns the banked record."""
    audio_hash = await generate_library_audio(text, voice)
    tokens = await asyncio.to_thread(tag_nouns_adjs, text)
    return content_bank.add_phrase(text, register, level, topic, voice, audio_hash,
                                   style=style, noun_adj_tokens=tokens)


# ── Schemas ────────────────────────────────────────────────────────────────────

class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None  # a Chirp3-HD voice name opts into the cached library


class ShadowPhraseRequest(BaseModel):
    level: str = 'A1'
    topic: Optional[str] = None
    style: Optional[str] = 'story'
    sound_focus: Optional[str] = None
    focus_word: Optional[str] = None
    # analytics / bank novelty
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None


class ShadowPhraseResponse(BaseModel):
    phrase: str
    audio_url: str
    level: str
    noun_adj_tokens: list = []


class ShadowAnalyzeRequest(BaseModel):
    target: str
    transcription: str
    confidence: Optional[float] = None
    noun_adj_tokens: Optional[list] = None
    # analytics fields
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None
    level: Optional[str] = None
    topic: Optional[str] = None
    attempt_number: Optional[int] = None
    phrase_id: Optional[str] = None
    listen_count: Optional[int] = None
    sound_focus: Optional[str] = None


class ShadowFeedbackItem(BaseModel):
    target_word: str
    said: str
    tip: str
    is_grammar: bool
    grammar_note: str


class WordResult(BaseModel):
    word: str
    matched: bool
    said: str


class ShadowAnalyzeResponse(BaseModel):
    score: float
    passed: bool
    feedback: list[ShadowFeedbackItem]
    word_results: list[WordResult]
    display_results: list[WordResult]




class ParagraphStartRequest(BaseModel):
    level: str = "A1"
    topic: Optional[str] = None
    style: Optional[str] = "story"


class ParagraphStartResponse(BaseModel):
    sentences: list[str]
    full_audio_url: Optional[str] = None
    sentence_audio_urls: list[str] = []  # per-sentence Chirp audio, stitched for playback
    level: str
    topic: str
    noun_adj_tokens: list = []
    paragraph_id: str = ""


class ParagraphAnalyzeRequest(BaseModel):
    target: str
    transcription: str
    confidence: Optional[float] = None
    chunk_size: int = 1
    noun_adj_tokens: Optional[list] = None
    # analytics fields
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None
    paragraph_id: Optional[str] = None
    chunk_index: Optional[int] = None
    attempt_number: Optional[int] = None
    level: Optional[str] = None
    is_drill: Optional[bool] = None
    sentence_index: Optional[int] = None


class ParagraphAnalyzeResponse(BaseModel):
    score: float
    passed: bool
    feedback: list[ShadowFeedbackItem]
    word_results: list[WordResult]
    display_results: list[WordResult]
    sentence_scores: list[float] = []


class PatternItem(BaseModel):
    pattern: str
    explanation: str
    examples: list[str]
    count: Optional[int] = None


class ParagraphAnalyzePatternsRequest(BaseModel):
    mismatches: list[dict]  # list of { target_word, said }


class ParagraphAnalyzePatternsResponse(BaseModel):
    rule_based: list[PatternItem]
    ai_patterns: list[PatternItem]


class PracticeWordRequest(BaseModel):
    word: str
    tip: str
    source_phrase: Optional[str] = None
    article: Optional[str] = None
    entry_type: Optional[str] = None


class CustomSaveRequest(BaseModel):
    label: str
    text: str
    content_type: str = "phrase"  # "phrase" | "paragraph" | "story"


class CustomStartRequest(BaseModel):
    text: str
    content_type: str = "paragraph"


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(BASE_DIR / "static" / "landing.html")

@app.get("/login")
async def login_page():
    return FileResponse(BASE_DIR / "static" / "login.html")

@app.get("/app")
async def app_page():
    return FileResponse(BASE_DIR / "static" / "index.html")

@app.get("/admin")
async def admin_page():
    return FileResponse(BASE_DIR / "static" / "admin.html")


@app.get("/dev")
async def dev_launcher():
    return FileResponse(BASE_DIR / "static" / "dev.html")


class AccessCodeRequest(BaseModel):
    code: str

@app.post("/validate-code")
async def validate_code(req: AccessCodeRequest):
    return {"ok": False, "deprecated": True}


# ── Auth schemas ───────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    invite_token: Optional[str] = None
    registration_code: Optional[str] = None
    is_teacher: bool = False


class RefreshRequest(BaseModel):
    refresh_token: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AddTeacherStudentRequest(BaseModel):
    email: Optional[str] = None
    name: Optional[str] = ""
    lesson_days: Optional[list] = []
    lesson_time: Optional[str] = ""
    notes: Optional[str] = ""


class InviteStudentRequest(BaseModel):
    email: str


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[int] = None


class CreateTeacherRequest(BaseModel):
    email: str
    name: Optional[str] = ""
    password: str


class AdminEmailRequest(BaseModel):
    subject: str
    body: str


# ── Auth routes ────────────────────────────────────────────────────────────────

def _make_token_response(user: dict) -> dict:
    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "access_code": user.get("access_code") or "",
    }
    access_token = _auth.create_access_token(payload)
    raw_refresh, refresh_hash, expires_at = _auth.create_refresh_token(user["id"])
    _analytics.store_refresh_token(user["id"], refresh_hash, expires_at)
    return {
        "access_token": access_token,
        "refresh_token": raw_refresh,
        "role": user["role"],
        "access_code": user.get("access_code") or "",
        "force_pw_change": bool(user.get("force_pw_change")),
    }


@app.post("/auth/login")
async def auth_login(req: LoginRequest):
    identifier = req.email.strip().lower()
    user = _analytics.get_user_by_email(identifier)
    if not user:
        user = _analytics.get_user_by_username(identifier)
    if not user or not _auth.verify_password(req.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account is deactivated")
    return _make_token_response(user)


@app.post("/auth/register")
async def auth_register(req: RegisterRequest):
    email = req.email.strip().lower()
    if not email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password required")
    if _analytics.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="Email already registered")

    invite = None
    teacher_id = None
    if req.invite_token:
        invite = _analytics.get_invite_token(req.invite_token.strip())
        if not invite:
            raise HTTPException(status_code=400, detail="Invite link is invalid or has expired")
        if invite["email"].lower() != email:
            raise HTTPException(status_code=400, detail="This invite was sent to a different email address")
        teacher_id = invite["teacher_id"]

    if not invite and _ACCESS_CODES and req.registration_code not in _ACCESS_CODES:
        raise HTTPException(status_code=403, detail="Invalid registration code")

    if req.is_teacher:
        role = "teacher"
    elif invite and invite.get("teacher_id"):
        role = "student_teacher"
    else:
        role = "student_solo"

    access_code = _auth.generate_access_code()
    user = _analytics.create_user(
        role=role,
        email=email,
        password_hash=_auth.hash_password(req.password),
        access_code=access_code,
        teacher_id=teacher_id,
    )

    if invite:
        _analytics.mark_invite_used(req.invite_token.strip())
        _analytics.create_student_from_invite(invite, access_code)

    return _make_token_response(user)


@app.post("/auth/refresh")
async def auth_refresh(req: RefreshRequest):
    from passlib.context import CryptContext
    _ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    with __import__("sqlite3").connect(str(_analytics.DB_PATH)) as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute(
            "SELECT * FROM refresh_tokens WHERE expires_at > datetime('now') ORDER BY created_at DESC"
        ).fetchall()
    matched_row = None
    for row in rows:
        try:
            if _ctx.verify(req.refresh_token, row["token_hash"]):
                matched_row = dict(row)
                break
        except Exception:
            continue
    if not matched_row:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")
    user = _analytics.get_user_by_id(matched_row["user_id"])
    if not user or not user["is_active"]:
        raise HTTPException(status_code=401, detail="User not found or deactivated")
    payload = {
        "sub": str(user["id"]),
        "role": user["role"],
        "access_code": user.get("access_code") or "",
    }
    return {"access_token": _auth.create_access_token(payload)}


@app.post("/auth/logout")
async def auth_logout(req: RefreshRequest):
    from passlib.context import CryptContext
    _ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")
    with __import__("sqlite3").connect(str(_analytics.DB_PATH)) as conn:
        conn.row_factory = __import__("sqlite3").Row
        rows = conn.execute("SELECT * FROM refresh_tokens").fetchall()
    for row in rows:
        try:
            if _ctx.verify(req.refresh_token, row["token_hash"]):
                _analytics.delete_refresh_token(row["token_hash"])
                break
        except Exception:
            continue
    return {"ok": True}


@app.post("/auth/change-password")
async def auth_change_password(
    req: ChangePasswordRequest,
    current_user: dict = Depends(_auth.get_current_user),
):
    user = _analytics.get_user_by_id(int(current_user["sub"]))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not _auth.verify_password(req.current_password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Current password incorrect")
    _analytics.update_user_password(user["id"], _auth.hash_password(req.new_password))
    return {"ok": True}


class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

@app.post("/auth/forgot-password")
async def auth_forgot_password(req: ForgotPasswordRequest):
    import secrets as _secrets
    from datetime import datetime, timedelta
    email = req.email.strip().lower()
    user = _analytics.get_user_by_email(email)
    # Always return 200 to avoid leaking which emails are registered
    if not user:
        return {"ok": True}
    if _analytics.count_recent_reset_tokens(user["id"], within_hours=1) >= 5:
        return {"ok": True}  # silently swallow — don't reveal the limit to potential attackers
    token = _secrets.token_urlsafe(32)
    expires_at = (datetime.utcnow() + timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
    _analytics.create_password_reset_token(token, user["id"], expires_at)
    reset_url = f"{_APP_BASE_URL}/login?reset={token}"
    email_html = f"""
<p>Hi,</p>
<p>We received a request to reset your VraiFrench password. Click the link below to set a new password. This link expires in 1 hour.</p>
<p><a href="{reset_url}">{reset_url}</a></p>
<p>If you did not request this, you can ignore this email.</p>
"""
    await _send_email(email, "Reset your VraiFrench password", email_html)
    return {"ok": True}


@app.post("/auth/reset-password")
async def auth_reset_password(req: ResetPasswordRequest):
    if not req.new_password or len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    ok = _analytics.consume_password_reset_token(req.token, _auth.hash_password(req.new_password))
    if not ok:
        raise HTTPException(status_code=400, detail="Reset link is invalid or has expired")
    return {"ok": True}


# ── Teacher routes ─────────────────────────────────────────────────────────────

@app.get("/teacher/students")
async def teacher_list_students(
    teacher_id: Optional[int] = None,
    current_user: dict = Depends(_auth.require_teacher),
):
    if current_user["role"] == "super_admin" and teacher_id:
        return {"students": _analytics.get_students_for_teacher_user(teacher_id)}
    if current_user["role"] == "super_admin":
        return {"students": _analytics.get_all_users()}
    return {"students": _analytics.get_students_for_teacher_user(int(current_user["sub"]))}


@app.post("/teacher/students")
async def teacher_add_student(
    req: AddTeacherStudentRequest,
    current_user: dict = Depends(_auth.require_teacher),
):
    import secrets as _secrets
    from datetime import datetime, timedelta

    teacher_user_id = int(current_user["sub"])
    lesson_days = json.dumps(req.lesson_days or [])

    # ── No-email path: create account directly for children ──────────────────
    if not req.email:
        name = (req.name or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Name is required when no email is provided")

        # Generate a username from the student's name (lowercase, dots between words)
        import re as _re
        base = _re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".")
        username = base
        suffix = 1
        while _analytics.username_is_taken(username):
            username = f"{base}{suffix}"
            suffix += 1

        _, plain_password, hashed = _auth.generate_temp_credentials()
        # plain_password only; we ignore the random username from generate_temp_credentials
        access_code = _auth.generate_access_code()
        synthetic_email = f"student-{access_code}@noemail.local"

        user = _analytics.create_user(
            role="student_teacher",
            email=synthetic_email,
            password_hash=hashed,
            username=username,
            access_code=access_code,
            teacher_id=teacher_user_id,
            force_pw_change=0,
        )

        return {
            "ok": True,
            "no_email": True,
            "username": username,
            "password": plain_password,
            "access_code": access_code,
            "name": name,
        }

    # ── Email path: send invite ───────────────────────────────────────────────
    email = req.email.strip().lower()
    if _analytics.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    token = _secrets.token_hex(24)
    expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()

    _analytics.create_invite_token(
        token=token,
        teacher_id=teacher_user_id,
        email=email,
        expires_at=expires_at,
        name=req.name or "",
        lesson_days=lesson_days,
        lesson_time=req.lesson_time or "",
        notes=req.notes or "",
    )

    invite_url = f"{_APP_BASE_URL}/login?invite={token}&email={email}"

    teacher = _analytics.get_user_by_id(teacher_user_id)
    teacher_name = teacher.get("username") or teacher.get("email", "Your teacher")

    email_html = f"""
<p>Bonjour,</p>
<p>{escHtml(teacher_name)} has added you to <strong>VraiFrench</strong>, a French pronunciation training tool.</p>
<p>Click the link below to create your account:</p>
<p><a href="{invite_url}">{invite_url}</a></p>
<p>This invitation expires in 7 days.</p>
<p>— VraiFrench</p>
"""
    sent = await _send_email(email, "You've been added to VraiFrench", email_html)

    return {"ok": True, "no_email": False, "invite_url": invite_url, "email_sent": sent, "email": email}


@app.post("/teacher/students/{user_id}/reset-password")
async def teacher_reset_student_password(
    user_id: int,
    current_user: dict = Depends(_auth.require_teacher),
):
    student = _analytics.get_user_by_id(user_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if current_user["role"] != "super_admin" and student.get("teacher_id") != int(current_user["sub"]):
        raise HTTPException(status_code=403, detail="Forbidden")
    username, plain_password, hashed = _auth.generate_temp_credentials()
    _analytics.reset_user_password(user_id, hashed)
    with __import__("sqlite3").connect(str(_analytics.DB_PATH)) as conn:
        conn.execute("UPDATE users SET username=? WHERE id=?", (username, user_id))
    _analytics.delete_refresh_tokens_for_user(user_id)
    return {"ok": True, "username": username, "temp_password": plain_password}


@app.patch("/teacher/students/{user_id}/status")
async def teacher_set_student_status(
    user_id: int,
    req: UpdateUserRequest,
    current_user: dict = Depends(_auth.require_teacher),
):
    student = _analytics.get_user_by_id(user_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if current_user["role"] != "super_admin" and student.get("teacher_id") != int(current_user["sub"]):
        raise HTTPException(status_code=403, detail="Forbidden")
    if req.is_active is not None:
        if req.is_active:
            with __import__("sqlite3").connect(str(_analytics.DB_PATH)) as conn:
                conn.execute("UPDATE users SET is_active=1 WHERE id=?", (user_id,))
        else:
            _analytics.deactivate_user(user_id)
            _analytics.delete_refresh_tokens_for_user(user_id)
    return {"ok": True}


@app.delete("/teacher/students/{user_id}")
async def teacher_remove_student(
    user_id: int,
    current_user: dict = Depends(_auth.require_teacher),
):
    student = _analytics.get_user_by_id(user_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if current_user["role"] != "super_admin" and student.get("teacher_id") != int(current_user["sub"]):
        raise HTTPException(status_code=403, detail="Forbidden")
    _analytics.deactivate_user(user_id)
    _analytics.delete_refresh_tokens_for_user(user_id)
    return {"ok": True}


@app.delete("/teacher/students/{user_id}/permanent")
async def teacher_delete_student(
    user_id: int,
    current_user: dict = Depends(_auth.require_teacher),
):
    """Permanently delete a student and all their data. Irreversible."""
    student = _analytics.get_user_by_id(user_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found")
    if current_user["role"] != "super_admin" and student.get("teacher_id") != int(current_user["sub"]):
        raise HTTPException(status_code=403, detail="Forbidden")
    # Teachers may only delete the students they manage (student_teacher, incl. no-email
    # children). Independent/paying solo students can only be deleted by an admin.
    if current_user["role"] == "teacher" and student["role"] != "student_teacher":
        raise HTTPException(
            status_code=403,
            detail="Only an administrator can delete an independent (solo) student.",
        )
    if student["role"] not in ("student_teacher", "student_solo"):
        raise HTTPException(status_code=400, detail="Only student accounts can be deleted here")
    result = _analytics.delete_user(user_id)
    return {"ok": result["deleted"]}


@app.post("/teacher/invite")
async def teacher_invite_student(
    req: InviteStudentRequest,
    current_user: dict = Depends(_auth.require_teacher),
):
    import secrets as _secrets
    from datetime import datetime, timedelta

    email = req.email.strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    existing = _analytics.get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail="A user with this email already exists")

    token = _secrets.token_hex(24)
    expires_at = (datetime.utcnow() + timedelta(days=7)).isoformat()
    teacher_id = int(current_user["sub"])
    _analytics.create_invite_token(token, teacher_id, email, expires_at)

    invite_url = f"{_APP_BASE_URL}/login?invite={token}&email={email}"

    teacher = _analytics.get_user_by_id(teacher_id)
    teacher_name = teacher.get("username") or teacher.get("email", "Your teacher")

    email_html = f"""
<p>Bonjour,</p>
<p>{escHtml(teacher_name)} has invited you to join <strong>VraiFrench</strong>, a French pronunciation training tool.</p>
<p>Click the link below to create your account:</p>
<p><a href="{invite_url}">{invite_url}</a></p>
<p>This invitation expires in 7 days.</p>
<p>— VraiFrench</p>
"""
    sent = await _send_email(email, "You've been invited to VraiFrench", email_html)

    return {"ok": True, "invite_url": invite_url, "email_sent": sent}


def escHtml(s: str) -> str:
    return (str(s)
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ── Admin routes ───────────────────────────────────────────────────────────────

@app.get("/admin/users")
async def admin_list_users(current_user: dict = Depends(_auth.require_admin)):
    return {"users": _analytics.get_all_users()}


@app.get("/admin/teachers")
async def admin_list_teachers(current_user: dict = Depends(_auth.require_admin)):
    return {"teachers": _analytics.get_users_by_role("teacher")}


@app.post("/admin/teachers")
async def admin_create_teacher(
    req: CreateTeacherRequest,
    current_user: dict = Depends(_auth.require_admin),
):
    email = req.email.strip().lower()
    if not email or not req.password:
        raise HTTPException(status_code=400, detail="Email and password required")
    if _analytics.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="Email already registered")
    user = _analytics.create_user(
        role="teacher",
        email=email,
        password_hash=_auth.hash_password(req.password),
    )
    return {"ok": True, "user_id": user["id"], "email": email}


@app.put("/admin/users/{user_id}")
async def admin_update_user(
    user_id: int,
    req: UpdateUserRequest,
    current_user: dict = Depends(_auth.require_admin),
):
    if req.role is not None:
        _analytics.update_user_role(user_id, req.role)
    if req.is_active is not None:
        if req.is_active:
            with __import__("sqlite3").connect(str(_analytics.DB_PATH)) as conn:
                conn.execute("UPDATE users SET is_active=1 WHERE id=?", (user_id,))
        else:
            _analytics.deactivate_user(user_id)
    return {"ok": True}


@app.get("/admin/users/{user_id}/delete-impact")
async def admin_delete_impact(
    user_id: int,
    current_user: dict = Depends(_auth.require_admin),
):
    """Preview what a permanent delete will affect (for the confirmation modal)."""
    user = _analytics.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    breakdown = (
        _analytics.get_teacher_student_breakdown(user_id)
        if user["role"] == "teacher" else {"managed": 0, "children": 0, "solo": 0}
    )
    return {
        "role": user["role"],
        "email": user["email"],
        "username": user.get("username"),
        **breakdown,
    }


@app.delete("/admin/users/{user_id}")
async def admin_delete_user(
    user_id: int,
    current_user: dict = Depends(_auth.require_admin),
):
    """Permanently delete any user. Teachers cascade by student role (see analytics.delete_user). Irreversible."""
    user = _analytics.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user["role"] == "super_admin":
        raise HTTPException(status_code=403, detail="Super admin accounts cannot be deleted")
    if user_id == int(current_user["sub"]):
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    result = _analytics.delete_user(user_id)
    return {"ok": result["deleted"], **result}


@app.post("/admin/users/{user_id}/email")
async def admin_email_user(
    user_id: int,
    req: AdminEmailRequest,
    current_user: dict = Depends(_auth.require_admin),
):
    """Send a one-off email to a single user (e.g. a teacher) from the admin dashboard."""
    user = _analytics.get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    to = (user.get("email") or "").strip()
    if not to or to.endswith("@noemail.local"):
        raise HTTPException(status_code=400, detail="This user has no email address")
    subject = req.subject.strip()
    body = req.body.strip()
    if not subject or not body:
        raise HTTPException(status_code=400, detail="Subject and message are required")
    # Plain-text body → escaped HTML with line breaks preserved
    html = "<p>" + escHtml(body).replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"
    sent = await _send_email(to, subject, html)
    if not sent:
        raise HTTPException(
            status_code=502,
            detail="Email failed to send. Check SMTP2GO_API_KEY and the verified sender.",
        )
    return {"ok": True, "email": to}


@app.get("/admin/platform-stats")
async def admin_platform_stats(dataset: str = "current", current_user: dict = Depends(_auth.require_admin)):
    with _use_dataset(dataset):
        return _analytics.get_platform_stats()


@app.get("/admin/user-hierarchy")
async def admin_user_hierarchy(dataset: str = "current", current_user: dict = Depends(_auth.require_admin)):
    with _use_dataset(dataset):
        return _analytics.get_user_hierarchy()


@app.get("/admin/feature-usage")
async def admin_feature_usage(current_user: dict = Depends(_auth.require_admin)):
    return _analytics.get_feature_usage()


@app.get("/admin/content-pool")
async def admin_content_pool(current_user: dict = Depends(_auth.require_admin)):
    """Size the shared Chirp3-HD content bank (phrases, paragraphs, listening
    passages, dialogues) so an admin can see the reusable pool it's built up."""
    return await asyncio.to_thread(content_bank.bank_stats)


# ── Current user info ──────────────────────────────────────────────────────────

@app.get("/auth/me")
async def auth_me(current_user: dict = Depends(_auth.get_current_user)):
    user = _analytics.get_user_by_id(int(current_user["sub"]))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    next_lesson = None
    access_code = user.get("access_code")
    if access_code:
        import sqlite3 as _sqlite3
        with _sqlite3.connect(str(_analytics.DB_PATH)) as _c:
            _c.row_factory = _sqlite3.Row
            row = _c.execute(
                "SELECT lesson_days FROM students WHERE access_code=?", (access_code,)
            ).fetchone()
            if row:
                from datetime import date as _date
                next_lesson = _analytics.next_lesson_date(row["lesson_days"])
                next_lesson = next_lesson.isoformat() if next_lesson else None
    teacher_name = None
    if user.get("teacher_id"):
        teacher = _analytics.get_user_by_id(user["teacher_id"])
        if teacher:
            teacher_name = teacher.get("username") or teacher.get("email")

    return {
        "id": user["id"],
        "role": user["role"],
        "email": user["email"],
        "username": user.get("username"),
        "access_code": access_code,
        "force_pw_change": bool(user.get("force_pw_change")),
        "created_at": user.get("created_at"),
        "plan_name": user.get("plan_name"),
        "plan_price": user.get("plan_price"),
        "billing_date": user.get("billing_date"),
        "next_lesson": next_lesson,
        "teacher_name": teacher_name,
    }


class TrackRequest(BaseModel):
    session_id: str
    access_code: str
    event_type: str
    visit_id: Optional[str] = None
    payload: dict = {}

@app.post("/track")
async def track_event(req: TrackRequest, authorization: Optional[str] = Header(None)):
    # The client sends its localStorage copy of the access code, which can be
    # missing (never stored at login, cleared, or a stale tab) — that silently
    # orphans the event under an empty code and it disappears from every
    # per-student view. Fall back to the identity in the JWT.
    # Body first, so teach mode (teacher driving a student's session) still
    # attributes events to the student rather than to the teacher.
    access_code = req.access_code or ""
    if not access_code and authorization and authorization.startswith("Bearer "):
        try:
            access_code = _auth.decode_token(authorization[7:]).get("access_code") or ""
        except Exception:
            pass
    _analytics.track(req.session_id, access_code, req.event_type, req.payload, req.visit_id)
    return {"ok": True}


def _require_analytics_key(key: str = "", authorization: Optional[str] = Header(None)) -> dict:
    """Accept either the legacy ?key= query param or a Bearer JWT (teacher/super_admin)."""
    if key and key in _ANALYTICS_KEYS:
        return {"role": "teacher", "sub": None, "is_legacy": True}
    if authorization and authorization.startswith("Bearer "):
        payload = _auth.decode_token(authorization[7:])
        if payload.get("role") not in ("teacher", "super_admin"):
            raise HTTPException(status_code=403, detail="Forbidden")
        return payload
    raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/analytics")
async def get_analytics(auth: dict = Depends(_require_analytics_key)):
    return _analytics.get_analytics()


@app.get("/analytics/sessions")
async def get_session_history(access_code: str = "", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    return {"sessions": _analytics.get_session_history(access_code)}


@app.get("/analytics/word-accuracy/download")
async def download_word_accuracy(access_code: str = "", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    words = _analytics.get_word_accuracy(access_code)
    lines = ["word,attempts,accuracy_pct"]
    for w in words:
        lines.append(f"{w['word']},{w['attempts']},{round(w['accuracy'] * 100, 1)}")
    csv_content = "\n".join(lines)
    filename = f"struggles_{access_code}.csv"
    return Response(
        content=csv_content,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/analytics/reset")
async def reset_analytics(access_code: str = "", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    deleted = _analytics.delete_events(access_code)
    return {"deleted_events": deleted}


@app.get("/coach")
async def coach_data(access_code: str = ""):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    cached = _analytics.get_cached_coach(access_code)
    if cached:
        return cached
    data = _analytics.get_coach_data(access_code)
    _analytics.set_cached_coach(access_code, data)
    return data


@app.post("/coach/refresh")
async def coach_refresh(access_code: str = ""):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    data = _analytics.get_coach_data(access_code)
    _analytics.set_cached_coach(access_code, data)
    return data


@app.get("/analytics/progress")
async def analytics_progress(access_code: str = "", days: int = 30):
    """Student-facing progress data for the landing page.

    Access-code only (no teacher key), mirroring /coach — the student tool has
    no analytics key. Returns the per-type/per-level score trend, the cumulative
    words-mastered curve, and a small headline summary.
    """
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    since_days = max(1, min(days, 365))
    return _analytics.get_home_data(access_code, since_days=since_days)


def _window_to_since_days(window: str, access_code: str) -> Optional[int]:
    """Map a dashboard window token to a since_days int (None = all time)."""
    if window == "30d":
        return 30
    if window == "since":
        s = _analytics.get_student_by_code(access_code)
        if s:
            ll = _analytics.last_lesson_date(s.get("lesson_days") or "[]")
            if ll:
                return max((date.today() - ll).days, 1)
        return 30  # no schedule → fall back to 30d
    return None  # "all"


def _window_to_since_date(window: str, access_code: str) -> date:
    """Map a dashboard window token to a `since` date for get_practice_since."""
    days = _window_to_since_days(window, access_code)
    if days is None:
        first = _analytics.get_first_event_ts(access_code)
        if first:
            try:
                return date.fromisoformat(first[:10]) - timedelta(days=1)
            except Exception:
                pass
        return date.today() - timedelta(days=3650)
    return date.today() - timedelta(days=days)


@app.get("/analytics/trend")
async def analytics_trend(access_code: str = "", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    return _analytics.get_score_trend(access_code)


@app.get("/analytics/practice")
async def analytics_practice(access_code: str = "", window: str = "since", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    since = _window_to_since_date(window, access_code)
    data = _analytics.get_practice_since(access_code, since)
    data["topics"] = [t["topic"] for t in _analytics.get_topic_coverage(access_code)[:6]]
    return data


@app.get("/analytics/paragraph")
async def analytics_paragraph(access_code: str = "", window: str = "all", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    return _analytics.get_paragraph_exercise_stats(
        access_code, since_days=_window_to_since_days(window, access_code))


@app.get("/analytics/phrase")
async def analytics_phrase(access_code: str = "", window: str = "all", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    return _analytics.get_phrase_exercise_stats(
        access_code, since_days=_window_to_since_days(window, access_code))


@app.get("/analytics/words")
async def analytics_words(access_code: str = "", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    return {"words": _analytics.get_word_accuracy(access_code)}


@app.get("/analytics/recent-struggles")
async def analytics_recent_struggles(access_code: str = "", sessions: int = 3, auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    return {"words": _analytics.get_recent_struggles(access_code, sessions=sessions)}


@app.get("/analytics/content")
async def analytics_content(access_code: str = "", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    return {
        "topics": _analytics.get_topic_coverage(access_code),
        "listen_speak": _analytics.get_listen_speak_ratio(access_code),
    }


@app.get("/analytics/exercises")
async def analytics_exercises(access_code: str = "", window: str = "30d", auth: dict = Depends(_require_analytics_key)):
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    return _analytics.get_exercise_stats(
        access_code, since_days=_window_to_since_days(window, access_code))


class AddStudentRequest(BaseModel):
    name: str
    email: str = ""
    lesson_days: list = []
    lesson_time: str = ""
    notes: str = ""


class UpdateStudentRequest(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    lesson_days: Optional[list] = None
    lesson_time: Optional[str] = None
    notes: Optional[str] = None


@app.get("/analytics/students")
async def list_students(
    teacher_id: Optional[int] = None,
    student: str = "",
    auth: dict = Depends(_require_analytics_key),
):
    # Super-admin scoping (from the admin panel): a single student's detail, or
    # a specific teacher's roster. Ignored for non-super callers so a teacher
    # can't peek at another teacher's roster via these params.
    if auth.get("role") == "super_admin":
        if student:
            return {"students": _analytics.get_roster(allowed_codes={student})}
        if teacher_id:
            teacher_students = _analytics.get_students_for_teacher_user(teacher_id)
            allowed = {s["access_code"] for s in teacher_students if s.get("access_code")}
            return {"students": _analytics.get_roster(allowed_codes=allowed)}
    if not auth.get("is_legacy") and auth.get("sub"):
        # JWT-authenticated teacher: only show their own students (prevents legacy code bleed-through)
        teacher_students = _analytics.get_students_for_teacher_user(int(auth["sub"]))
        allowed = {s["access_code"] for s in teacher_students if s.get("access_code")}
        return {"students": _analytics.get_roster(allowed_codes=allowed)}
    return {"students": _analytics.get_roster()}


@app.post("/analytics/students")
async def add_student(req: AddStudentRequest, auth: dict = Depends(_require_analytics_key)):
    return _analytics.add_student(
        req.name, req.email, req.lesson_days, req.lesson_time, req.notes,
    )


@app.get("/analytics/students/seed")
async def seed_students(codes: str = "", auth: dict = Depends(_require_analytics_key)):
    """Insert student rows for comma-separated access codes that don't already exist."""
    if not codes:
        raise HTTPException(status_code=400, detail="codes required")
    return _analytics.seed_students([c.strip() for c in codes.split(",") if c.strip()])


@app.put("/analytics/students/{access_code}")
async def update_student(access_code: str, req: UpdateStudentRequest, auth: dict = Depends(_require_analytics_key)):
    updated = _analytics.update_student(
        access_code,
        name=req.name, email=req.email,
        lesson_days=req.lesson_days, lesson_time=req.lesson_time,
        notes=req.notes,
    )
    if not updated:
        raise HTTPException(status_code=404, detail="Student not found")
    return {"ok": True}


@app.get("/dashboard")
async def dashboard_shortcut():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/static/analytics.html")


@app.get("/analytics/dashboard")
async def analytics_dashboard():
    return FileResponse(BASE_DIR / "static" / "analytics.html")


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    if not re.fullmatch(r"[a-f0-9]{32}\.mp3", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = AUDIO_DIR / filename
    if path.exists():
        return FileResponse(str(path), media_type="audio/mpeg")
    # Not an ephemeral edge-tts file — try the persistent Chirp3 library (R2/local).
    data = await asyncio.to_thread(library_store.get_audio, filename)
    if data is not None:
        return Response(content=data, media_type="audio/mpeg")
    raise HTTPException(status_code=404, detail="Audio not found")


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported")
    safe_name = Path(file.filename).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    UPLOADS_DIR.mkdir(exist_ok=True)
    (UPLOADS_DIR / safe_name).write_bytes(await file.read())
    return {"filename": safe_name, "status": "uploaded"}


@app.get("/uploads")
async def list_uploads():
    if not UPLOADS_DIR.exists():
        return {"files": []}
    return {"files": sorted(f.name for f in UPLOADS_DIR.glob("*.pdf"))}


@app.delete("/uploads/{filename}")
async def delete_upload(filename: str):
    safe_name = Path(filename).name
    path = UPLOADS_DIR / safe_name
    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    path.unlink()
    return {"status": "deleted"}


# ── TTS route ──────────────────────────────────────────────────────────────────

@app.post("/tts")
async def tts_word(req: TTSRequest):
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    # A Chirp3-HD voice (or the "chirp-random" sentinel) routes to the cached
    # listening library; anything else (word/phrase pronunciation, paragraph
    # chunks, …) stays on free edge-tts.
    if req.voice == _CHIRP_RANDOM:
        voice = pick_narrator_voice()
    elif req.voice == _CHIRP_DEFAULT:
        voice = DEFAULT_CHIRP_VOICE
    else:
        voice = req.voice
    if voice and voice.startswith(_CHIRP_PREFIX):
        filename = await generate_library_audio(req.text.strip(), voice)
    else:
        filename = await generate_audio(req.text.strip())
    return {"audio_url": f"/audio/{filename}"}


def _build_noun_adj_set(tokens):
    """Convert Mistral's noun_adj_tokens list into a set of base forms (without terminal -s)."""
    result = set()
    for t in (tokens or []):
        t_lower = t.lower()
        result.add(t_lower[:-1] if t_lower.endswith("s") else t_lower)
    return result


# ── Shared phrase helpers ──────────────────────────────────────────────────────

async def _phrase_generate(req: ShadowPhraseRequest) -> ShadowPhraseResponse:
    style = req.style or 'story'
    # Sound-focus / coach-focus requests need a phrase tailored to that focus (and
    # liaison focus injects ‿ marks), so they bypass the shared pool. Plain requests
    # serve an unseen banked phrase first and only generate + bank on exhaustion.
    is_focus = bool(req.sound_focus or req.focus_word)
    if not is_focus:
        topic = req.topic or random.choice(TOPICS)
        rec = _bank_pick("phrase", "standard", req.level, topic, "", req.access_code)
        if rec is None:
            gen = await asyncio.to_thread(lambda: generate_phrase(req.level, topic, style))
            rec = await _synth_and_bank_phrase(
                gen["phrase"], "standard", req.level, topic, style, pick_narrator_voice(),
            )
        _analytics.mark_bank_seen(req.access_code, rec["id"], "shadow")
        return ShadowPhraseResponse(
            phrase=rec["text"],
            audio_url=f"/audio/{rec['audio_hash']}",
            level=req.level,
            noun_adj_tokens=rec.get("noun_adj_tokens", []),
        )

    data = await asyncio.to_thread(lambda: generate_phrase(req.level, req.topic, style, req.sound_focus, req.focus_word))
    audio_url = f"/audio/{await generate_library_audio(data['phrase'], pick_narrator_voice())}"
    return ShadowPhraseResponse(
        phrase=data["phrase"],
        audio_url=audio_url,
        level=req.level,
        noun_adj_tokens=data.get("noun_adj_tokens", []),
    )


async def _phrase_analyze(req: ShadowAnalyzeRequest, exercise_type: str) -> ShadowAnalyzeResponse:
    noun_adj_set = _build_noun_adj_set(req.noun_adj_tokens)
    result = score_attempt(req.target, req.transcription, noun_adj_set)
    if req.session_id and req.access_code:
        _analytics.track(req.session_id, req.access_code, "phrase_attempted", {
            "exercise_type": exercise_type,
            "level": req.level,
            "topic": req.topic,
            "score": result["score"],
            "passed": result["passed"],
            "attempt_number": req.attempt_number,
            "phrase_id": req.phrase_id,
            "listen_count": req.listen_count,
            "sound_focus": req.sound_focus,
            "stt_confidence": req.confidence,
            "word_results": [[wr["word"], wr["matched"], wr.get("said", "")] for wr in result["word_results"]],
        }, req.visit_id)
    feedback_raw = await asyncio.to_thread(
        lambda: analyze_shadow_mismatches(req.target, req.transcription, result["mismatches"])
    )
    feedback = [
        ShadowFeedbackItem(
            target_word=f.get("target_word", ""),
            said=f.get("said", ""),
            tip=f.get("tip", ""),
            is_grammar=f.get("is_grammar", False),
            grammar_note=f.get("grammar_note", ""),
        )
        for f in feedback_raw
    ]
    word_results = [WordResult(word=wr["word"], matched=wr["matched"], said=wr["said"]) for wr in result["word_results"]]
    display_results = [WordResult(word=dr["word"], matched=dr["matched"], said=dr["said"]) for dr in result["display_results"]]
    return ShadowAnalyzeResponse(
        score=result["score"],
        passed=result["passed"],
        feedback=feedback,
        word_results=word_results,
        display_results=display_results,
    )


# ── Speaking routes ────────────────────────────────────────────────────────────

@app.post("/speaking/phrase", response_model=ShadowPhraseResponse)
async def speaking_phrase(req: ShadowPhraseRequest):
    try:
        return await _phrase_generate(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Phrase generation failed: {e}")


@app.post("/speaking/analyze", response_model=ShadowAnalyzeResponse)
async def speaking_analyze(req: ShadowAnalyzeRequest):
    return await _phrase_analyze(req, "speaking")


class ShadowRhythmRequest(BaseModel):
    phrase: str


@app.post("/speaking/rhythm")
async def speaking_rhythm(req: ShadowRhythmRequest):
    try:
        data = await asyncio.to_thread(lambda: annotate_phrase_rhythm(req.phrase))
        return data
    except Exception as e:
        print(f"[speaking/rhythm] ERROR for phrase={req.phrase!r}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Rhythm annotation failed: {e}")


# ── Shadow routes (true simultaneous shadowing exercise) ───────────────────────

@app.post("/shadow/phrase", response_model=ShadowPhraseResponse)
async def shadow_phrase(req: ShadowPhraseRequest):
    try:
        return await _phrase_generate(req)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Phrase generation failed: {e}")


@app.post("/shadow/analyze", response_model=ShadowAnalyzeResponse)
async def shadow_analyze(req: ShadowAnalyzeRequest):
    return await _phrase_analyze(req, "shadow")


class WordDrillAnalyzeRequest(BaseModel):
    word: str
    attempts: list
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None
    level: Optional[str] = None
    source: Optional[str] = None  # "practice_list" | "phrase_exercise" | "paragraph_drill"
    mode: Optional[str] = None    # "check" | "drill"


_WORD_DRILL_SYSTEM_STRUGGLING = """You are a French pronunciation coach analyzing a student's repeated attempts to say a single word.

You are given:
- The target word (what they should have said)
- A list of transcribed attempts (what speech recognition captured each time)
- A hit rate: the fraction of attempts where speech recognition matched the target

Identify the consistent pattern across attempts — what phoneme or feature is the student struggling with.

Return a concise coaching note (2-4 sentences) covering:
1. The specific sound or pattern they're missing
2. One body-mechanics cue (lip/tongue/nasal position)
3. One practical tip to improve

Be direct and specific. No preamble. Plain text, no markdown."""

_WORD_DRILL_SYSTEM_SOLID = """You are a French pronunciation coach reviewing a student's drill results for a single word.

You are given:
- The target word
- A list of transcribed attempts
- A hit rate: the fraction of attempts where speech recognition matched the target

The student got this word right most of the time. Give brief, encouraging feedback that:
1. Confirms what they're doing well (1 sentence)
2. Notes any minor inconsistency worth watching, or a refinement tip if all attempts were perfect

Keep it to 2 sentences max. Be specific to the word. No preamble. Plain text, no markdown."""


def _word_drill_hit_rate(word: str, attempts: list) -> float:
    target = re.sub(r"[^\w]", "", word.lower())
    hits = sum(1 for a in attempts if re.sub(r"[^\w]", "", a.lower()) == target)
    return hits / len(attempts) if attempts else 0.0


@app.post("/analyze_word_drill")
async def analyze_word_drill(req: WordDrillAnalyzeRequest):
    if not req.word or not req.attempts:
        return {"feedback": "No attempts to analyze."}

    hit_rate = _word_drill_hit_rate(req.word, req.attempts)
    if req.session_id and req.access_code:
        _analytics.track(req.session_id, req.access_code, "word_attempted", {
            "exercise_type": "word",
            "mode": req.mode or "drill",
            "source": req.source,
            "word": req.word,
            "level": req.level,
            "attempts": len(req.attempts),
            "score": round(hit_rate, 3),
        }, req.visit_id)
    system = _WORD_DRILL_SYSTEM_SOLID if hit_rate >= 0.6 else _WORD_DRILL_SYSTEM_STRUGGLING
    attempts_text = "\n".join(f"- {a}" for a in req.attempts)
    prompt = f"Target word: {req.word}\nHit rate: {hit_rate:.0%}\n\nAttempts:\n{attempts_text}"

    try:
        def _call():
            client = Mistral(api_key=os.environ["MISTRAL_API_KEY"])
            resp = client.chat.complete(
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        feedback = await asyncio.to_thread(_call)
        return {"feedback": feedback}
    except Exception as e:
        print(f"[analyze_word_drill] ERROR: {e}")
        return {"feedback": "Analysis unavailable."}


# ── Paragraph shadow routes ────────────────────────────────────────────────────

class ParagraphStartRequestWithSession(ParagraphStartRequest):
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None

@app.post("/paragraph/start", response_model=ParagraphStartResponse)
async def paragraph_start(req: ParagraphStartRequestWithSession):
    topic = req.topic or random.choice(TOPICS)
    style = req.style or 'story'
    try:
        # Serve a banked passage per the reuse policy; generate + bank (which also
        # seeds the reusable phrase pool) when the policy calls for new content.
        passage = _bank_pick("passage", "standard", req.level, topic, style, req.access_code)
        if passage is None:
            passage = await _generate_and_bank_passage(req.level, topic, style)

        phrases = content_bank.passage_phrases(passage)
        sentences = [p["text"] for p in phrases]
        sentence_audio_urls = [f"/audio/{p['audio_hash']}" for p in phrases]
        paragraph_id = passage["id"]

        _analytics.mark_bank_seen(req.access_code, paragraph_id, "paragraph")
        if req.session_id and req.access_code:
            _analytics.track(req.session_id, req.access_code, "paragraph_started", {
                "exercise_type": "paragraph",
                "paragraph_id": paragraph_id,
                "level": req.level,
                "topic": topic,
                "sentence_count": len(sentences),
            }, req.visit_id)
        return ParagraphStartResponse(
            sentences=sentences,
            sentence_audio_urls=sentence_audio_urls,
            level=passage.get("level", req.level),
            topic=topic,
            noun_adj_tokens=passage.get("noun_adj_tokens", []),
            paragraph_id=paragraph_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Paragraph generation failed: {e}")


def _dialogue_voice_sequence(sentences: list, voice_a: str, voice_b: str) -> list:
    """Assign a Chirp voice to each sentence of a dialogue passage, switching speaker
    at every em-dash turn marker so the two voices alternate per turn (a turn may span
    several sentences). Falls back to a single voice if no turn markers are present."""
    voices = []
    current = voice_a
    started = False
    for sent in sentences:
        stripped = sent.lstrip()
        is_turn = stripped[:1] in ("—", "–") or stripped[:2] == "- "
        if is_turn:
            if started:
                current = voice_b if current == voice_a else voice_a
            started = True
        voices.append(current)
    return voices


async def _generate_and_bank_passage(level: str, topic: str, style: str) -> dict:
    """Generate a cohesive paragraph, synthesize each sentence as its own Chirp
    phrase, and bank a PASSAGE + its PHRASEs. Generating a paragraph thus seeds the
    reusable phrase pool. Dialogue passages get two alternating Chirp voices (one per
    speaker turn, like Listen & Answer's Dialogue French); every other style uses a
    single narrator. Returns the banked passage."""
    data = await asyncio.to_thread(lambda: generate_paragraph(level, topic, style))
    sentences = data["sentences"]
    if style == "dialogue":
        va, vb = pick_dialogue_voices()
        voices = _dialogue_voice_sequence(sentences, va, vb)
        passage_voice = va
    else:
        passage_voice = pick_narrator_voice()
        voices = [passage_voice] * len(sentences)
    phrase_ids = []
    for sent, voice in zip(sentences, voices):
        rec = await _synth_and_bank_phrase(sent, "standard", level, topic, style, voice)
        phrase_ids.append(rec["id"])
    return content_bank.add_passage(
        "standard", level, topic, passage_voice, phrase_ids,
        style=style, noun_adj_tokens=data.get("noun_adj_tokens", []),
    )


@app.post("/paragraph/analyze", response_model=ParagraphAnalyzeResponse)
async def paragraph_analyze(req: ParagraphAnalyzeRequest):
    noun_adj_set = _build_noun_adj_set(req.noun_adj_tokens)
    result = score_chunk(req.target, req.transcription, req.chunk_size, noun_adj_set)
    if req.session_id and req.access_code:
        if req.is_drill:
            _analytics.track(req.session_id, req.access_code, "paragraph_drilled", {
                "exercise_type": "paragraph",
                "paragraph_id": req.paragraph_id,
                "chunk_index": req.chunk_index,
                "sentence_index": req.sentence_index,
                "level": req.level,
                "score": result["score"],
                "attempt_number": req.attempt_number,
                "stt_confidence": req.confidence,
                "word_results": [[wr["word"], wr["matched"], wr.get("said", "")] for wr in result["word_results"]],
            }, req.visit_id)
        else:
            _analytics.track(req.session_id, req.access_code, "paragraph_attempted", {
                "exercise_type": "paragraph",
                "paragraph_id": req.paragraph_id,
                "chunk_index": req.chunk_index,
                "chunk_size": req.chunk_size,
                "level": req.level,
                "score": result["score"],
                "attempt_number": req.attempt_number,
                "stt_confidence": req.confidence,
                "word_results": [[wr["word"], wr["matched"], wr.get("said", "")] for wr in result["word_results"]],
            }, req.visit_id)
    feedback_raw = await asyncio.to_thread(
        lambda: analyze_mismatches(req.target, req.transcription, result.get("mismatches", []))
    )
    feedback = [
        ShadowFeedbackItem(
            target_word=f.get("target_word", ""),
            said=f.get("said", ""),
            tip=f.get("tip", ""),
            is_grammar=f.get("is_grammar", False),
            grammar_note=f.get("grammar_note", ""),
        )
        for f in feedback_raw
    ]
    word_results = [
        WordResult(word=wr["word"], matched=wr["matched"], said=wr["said"])
        for wr in result["word_results"]
    ]
    display_results = [
        WordResult(word=dr["word"], matched=dr["matched"], said=dr["said"])
        for dr in result["display_results"]
    ]
    return ParagraphAnalyzeResponse(
        score=result["score"],
        passed=result["passed"],
        feedback=feedback,
        word_results=word_results,
        display_results=display_results,
        sentence_scores=result.get("sentence_scores", []),
    )


@app.post("/paragraph/analyze-patterns", response_model=ParagraphAnalyzePatternsResponse)
async def paragraph_analyze_patterns(req: ParagraphAnalyzePatternsRequest):
    result = await asyncio.to_thread(lambda: analyze_patterns(req.mismatches))
    rule_based = [
        PatternItem(
            pattern=p["pattern"],
            explanation=p["explanation"],
            examples=p["examples"],
            count=p.get("count"),
        )
        for p in result["rule_based"]
    ]
    ai_patterns = [
        PatternItem(
            pattern=p["pattern"],
            explanation=p["explanation"],
            examples=p.get("examples", []),
        )
        for p in result["ai_patterns"]
    ]
    return ParagraphAnalyzePatternsResponse(
        rule_based=rule_based,
        ai_patterns=ai_patterns,
    )


# ── Custom content routes ──────────────────────────────────────────────────────

USER_CONTENT_FILE = BASE_DIR / "user_content.json"


def _load_user_content() -> list:
    if not USER_CONTENT_FILE.exists():
        return []
    try:
        return json.loads(USER_CONTENT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _save_user_content(entries: list) -> None:
    USER_CONTENT_FILE.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


def _split_sentences(text: str) -> list:
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return [p.strip() for p in parts if p.strip()]


def _split_phrases(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _split_stories(text: str) -> list:
    parts = re.split(r'\n[ \t]*-{4,}[ \t]*\n', '\n' + text.strip() + '\n')
    return [p.strip() for p in parts if p.strip()]


@app.get("/custom/list")
async def custom_list():
    entries = _load_user_content()
    for e in entries:
        ct = e.get("content_type", "paragraph")
        if ct == "story":
            e["story_count"] = len(e.get("stories", []))
        else:
            e["sentence_count"] = len(e.get("sentences", []))
    return {"entries": entries}


@app.post("/custom/save")
async def custom_save(req: CustomSaveRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    valid_types = {"phrase", "paragraph", "story"}
    content_type = req.content_type if req.content_type in valid_types else "paragraph"
    entries = _load_user_content()
    entry = {
        "id": str(uuid.uuid4()),
        "label": req.label.strip() or "Untitled",
        "text": req.text.strip(),
        "content_type": content_type,
        "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    if content_type == "phrase":
        entry["sentences"] = _split_phrases(req.text.strip())
    elif content_type == "story":
        raw_stories = _split_stories(req.text.strip())
        entry["stories"] = [{"text": s, "sentences": _split_sentences(s)} for s in raw_stories]
        entry["sentences"] = []
    else:
        entry["sentences"] = _split_sentences(req.text.strip())
    entries.insert(0, entry)
    _save_user_content(entries)
    return {"status": "ok", "entry": entry}


@app.delete("/custom/{entry_id}")
async def custom_delete(entry_id: str):
    entries = _load_user_content()
    entries = [e for e in entries if e["id"] != entry_id]
    _save_user_content(entries)
    return {"status": "deleted"}


@app.post("/custom/start")
async def custom_start(req: CustomStartRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    valid_types = {"phrase", "paragraph", "story"}
    content_type = req.content_type if req.content_type in valid_types else "paragraph"
    if content_type == "phrase":
        sentences = _split_phrases(req.text.strip())
    else:
        sentences = _split_sentences(req.text.strip())
    if not sentences:
        raise HTTPException(status_code=400, detail="No content found in text")
    if content_type == "phrase":
        return {"sentences": sentences, "full_audio_url": None, "content_type": "phrase"}
    audio_file = await generate_library_audio(req.text.strip(), DEFAULT_CHIRP_VOICE)
    return {
        "sentences": sentences,
        "full_audio_url": f"/audio/{audio_file}",
        "content_type": content_type,
    }


# ── Practice list routes ────────────────────────────────────────────────────────

@app.get("/practice-list")
async def get_practice_list():
    return {"items": pl.get_all()}


@app.post("/practice-list")
async def add_to_practice_list(req: PracticeWordRequest):
    entry_type = req.entry_type if req.entry_type in ("word", "phrase", "paragraph") else "word"
    entry = pl.add_word(req.word, req.tip, req.source_phrase, req.article, entry_type)
    return {"status": "ok", "item": entry}


@app.get("/practice-list/pronunciation")
async def get_word_pronunciation(word: str):
    prompt = (
        f'For the French word or phrase "{word}", return a JSON object with two fields:\n'
        '- "tip": the word followed by its IPA transcription in slashes, em-dash, one body-mechanics cue '
        '(lip position, tongue placement, nasal vs. oral airflow, or silent letter). Max 20 words total.\n'
        '- "article": the correct definite article — le, la, l\', or les. '
        'For a verb use "je". For a fixed phrase with no natural article use "".\n\n'
        'Examples:\n'
        '{"tip": "cathédrale /ka.te.dʁal/ — uvular \'r\', final \'e\' is silent", "article": "la"}\n'
        '{"tip": "pain /pɛ̃/ — nasal vowel, mouth slightly open, no N sound at the end", "article": "le"}\n'
        '{"tip": "m\'appelle /ma.pɛl/ — lips forward on the \'a\', final \'l\' is light", "article": "je"}\n\n'
        'Return only the JSON object. No extra text.'
    )
    if _mistral is None:
        raise HTTPException(status_code=503, detail="API key not configured")
    resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
        model="mistral-small-latest",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=120,
    ))
    content = resp.choices[0].message.content.strip()
    try:
        data = json.loads(content)
        tip = data.get("tip", "").strip()
        article = data.get("article", "").strip()
    except Exception:
        tip = content
        article = ""
    if article:
        pl.update_article(word, article)
    return {"tip": tip, "article": article}


@app.get("/practice-list/context-phrase")
async def get_context_phrase(word: str):
    prompt = (
        f'Generate one short, natural French sentence (8–14 words) that includes the word or phrase "{word}". '
        'The sentence should be conversational and help a learner hear the word in real flow. '
        'Return a JSON object with two fields: "phrase" (the French sentence) and "translation" (English translation). '
        'Example: {"phrase": "Le boulanger pétrit le pain chaque matin.", "translation": "The baker kneads the bread every morning."}\n'
        'Return only the JSON object. No extra text.'
    )
    if _mistral is None:
        raise HTTPException(status_code=503, detail="API key not configured")
    resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
        model="mistral-small-latest",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=120,
    ))
    content = resp.choices[0].message.content.strip()
    try:
        data = json.loads(content)
        return {"phrase": data.get("phrase", "").strip(), "translation": data.get("translation", "").strip()}
    except Exception:
        return {"phrase": content, "translation": ""}


@app.delete("/practice-list/{word}")
async def remove_from_practice_list(word: str):
    removed = pl.remove_word(word)
    if not removed:
        raise HTTPException(status_code=404, detail="Word not found")
    return {"status": "deleted"}


@app.delete("/practice-list/id/{entry_id}")
async def remove_practice_entry(entry_id: str):
    removed = pl.remove_entry(entry_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"status": "deleted"}


# ── Listen & Answer routes ──────────────────────────────────────────────────────

_COMPREHENSION_SYSTEM = """You are a French language content generator for learners.
Generate a French listening passage and comprehension questions at the requested CEFR level.
Return ONLY valid JSON with this exact structure — no markdown, no explanation, just JSON:
{
  "passage": "The full French passage text...",
  "vocab_preview": [
    { "word": "exact word or phrase from passage", "gloss": "brief French-only definition", "example": "the exact sentence from the passage containing this word" }
  ],
  "questions": [
    {
      "type": "literal",
      "question": "Question in French?",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct_index": 0,
      "explanation": "One or two sentences in French explaining why this answer is correct."
    }
  ]
}

Rules:
- Vocabulary and grammar must strictly match the CEFR level
- Write exactly the number of paragraphs requested by the user; each paragraph should be a natural length for that level
- A1: ~40 words per paragraph, present tense only, high-frequency vocabulary
- A2: ~60 words per paragraph, passé composé + near future, everyday vocabulary
- B1: ~90 words per paragraph, varied tenses, some idiomatic expressions
- B2: ~120 words per paragraph, nuance and opinion, formal register where appropriate
- C1: ~150 words per paragraph, complex structures, abstract vocabulary
- C2: ~180 words per paragraph, literary register, implicit meaning and irony
- vocab_preview: choose 4-6 key words or phrases directly from the passage. For each: "word" is the exact token as it appears in the passage, "gloss" is a brief French-only definition (no English, no translation), "example" is the exact sentence from the passage where the word appears
- Generate exactly the number of questions specified in the user prompt. Always include one question of each of these types — in this order:
    "literal"    — tests direct recall of a stated fact
    "inference"  — requires the listener to infer something not explicitly stated
    "vocabulary" — tests understanding of a specific word or phrase in context; quote the word in the question
    "main_idea"  — tests understanding of the overall message, tone, or purpose
  If more than 4 questions are needed, add additional "literal" or "inference" questions after the first four
- All questions, options, and explanations must be in French
- Distractors should be plausible but clearly wrong on close listening
- Do not number the options — just the text"""

_COMPREHENSION_Q_COUNT = {"A1": 3, "A2": 3, "B1": 4, "B2": 4, "C1": 5, "C2": 5}

@app.post("/listen/generate")
async def listen_generate(req: Request):
    data = await req.json()
    level = data.get("level", "B1")
    topic = data.get("topic", "la vie quotidienne")
    session_id = data.get("session_id")
    access_code = data.get("access_code")
    visit_id = data.get("visit_id")
    num_paragraphs = min(max(int(data.get("num_paragraphs", 2)), 1), 4)
    q_count = _COMPREHENSION_Q_COUNT.get(level, 4)

    # Listen & Answer keeps its own longer-passage bucket (register="listen"),
    # separate from the speaking phrase pool. Serve an unseen banked passage first;
    # generate + bank only on exhaustion. Audio stays lazy (frontend /tts) but with
    # the passage's fixed voice, so reuse is a content-addressed cache hit (free).
    rec = _bank_pick("passage", "listen", level, topic, "", access_code)
    if rec is not None:
        passage = rec.get("text", "")
        questions = rec.get("questions", [])
        vocab_preview = rec.get("vocab_preview", [])
        voice = rec.get("voice", _CHIRP_RANDOM)
    else:
        user_prompt = (
            f"CEFR level: {level}\n"
            f"Topic: {topic}\n"
            f"Number of paragraphs: {num_paragraphs}\n"
            f"Number of questions: {q_count}\n\n"
            "Generate the passage, vocab_preview, and comprehension questions now."
        )
        resp = _mistral.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": _COMPREHENSION_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            max_tokens=2400,
        )
        result = json.loads(resp.choices[0].message.content)
        passage = result.get("passage", "")
        questions = result.get("questions", [])
        vocab_preview = result.get("vocab_preview", [])

        # The model tends to put the correct answer first (position bias), so shuffle
        # each question's options and recompute correct_index for an even spread.
        for q in questions:
            opts = q.get("options")
            if not isinstance(opts, list) or len(opts) < 2:
                continue
            ci = q.get("correct_index", 0)
            if not isinstance(ci, int) or not (0 <= ci < len(opts)):
                ci = 0
            correct_opt = opts[ci]
            random.shuffle(opts)
            q["options"] = opts
            q["correct_index"] = opts.index(correct_opt)

        voice = pick_narrator_voice()
        rec = content_bank.add_passage(
            "listen", level, topic, voice, [], questions=questions,
            vocab_preview=vocab_preview, payload={"text": passage},
        )

    _analytics.mark_bank_seen(access_code, rec["id"], "listen")
    # Audio is generated lazily by the frontend (via /tts) so the passage and
    # questions can be shown immediately instead of waiting for TTS to render.
    if session_id and access_code:
        _analytics.track(session_id, access_code, "listen_answer_started", {
            "exercise_type": "listen_answer",
            "level": level,
            "topic": topic,
            "question_count": len(questions),
        }, visit_id)
    return {"passage": passage, "questions": questions, "vocab_preview": vocab_preview, "voice": voice}


# ── Natural French (casual spoken dialogue) routes ───────────────────────────────

# Dialogue register presets — the learner picks one in the hub. Each supplies a
# one-line scene framing (`intro`) and a REGISTER block that swaps in/out of the
# shared prompt below. Slang density is now a user choice, so lower levels can get
# clean everyday speech instead of heavy argot. The key doubles as the content-bank
# `style` so the three registers bank into separate buckets and never cross-pollinate.
_NATURAL_REGISTERS = {
    "conversational": {
        "intro": "the way two friends actually talk to each other in everyday life — casual "
                 "and relaxed, full of slang and spoken shortcuts, the kind a learner hears in "
                 "films, podcasts and real conversation, NOT clean textbook French",
        "rules": """REGISTER — CASUAL / SLANG (apply heavily, this is the whole point):
- The two speakers are friends: they use "tu".
- DROP the "ne" of negation everywhere: "j'ai pas", "je sais pas", "c'est pas", "y a personne".
- Spoken fillers and connectors, used naturally: "ben", "bah", "ouais", "du coup", "en fait", "genre", "quoi", "t'sais", "voilà", "bref", "carrément", "grave".
- Colloquial / slang vocabulary: "un truc", "un machin", "bosser", "bouffer", "kiffer", "chelou", "trop", "vachement", "c'est chaud", "ça marche".
- Casual reactions and interruptions: "ah ouais ?", "non mais grave", "ah bon ?", "sérieux ?".""",
    },
    "everyday": {
        "intro": "natural, everyday spoken French between two people — clear and relaxed but "
                 "WITHOUT slang, the kind of plain conversation a learner needs to follow real "
                 "life (a shop, a café, asking directions, family) without getting lost in argot",
        "rules": """REGISTER — EVERYDAY / CLEAR (natural but low-slang — this is the point):
- The priority is CLARITY. This register is for lower levels, so keep it plain and easy to follow.
- Dropping "ne" is fine and natural ("j'ai pas", "je sais pas", "c'est pas").
- A FEW light, common fillers are OK, used SPARINGLY: "ben", "alors", "du coup", "voilà", "bon". Do not pack them in.
- Use STANDARD everyday vocabulary. Do NOT use slang: avoid "kiffer", "bosser", "bouffer", "chelou", "vachement", "grave", "un truc", "un machin", "carrément".
- Use "tu" between friends or family, "vous" with a stranger or in a shop — match it to the situation.
- Keep sentences short and concrete.""",
    },
    "professional": {
        "intro": "spoken French in a professional / workplace setting — polite and standard, the "
                 "register used with colleagues, clients and in meetings: natural and spoken but "
                 "never slangy",
        "rules": """REGISTER — PROFESSIONAL / POLITE (standard workplace French):
- The speakers use "vous" and stay polite and courteous throughout.
- Standard register: keeping the full "ne...pas" or lightly reducing it is fine, but NO slang and NO casual fillers ("ouais", "grave", "kiffer", "bosser", "chelou" are all forbidden).
- Use professional / workplace vocabulary suited to the situation (réunion, dossier, projet, client, échéance, collègue, entretien).
- Polite forms and hedging: "je vous en prie", "pourriez-vous", "je pense que", "il faudrait peut-être", "si possible".
- Still spoken and natural — not a written report — but measured and clear, not chatty.""",
    },
}


def _natural_system(dtype: str) -> str:
    """Build the Dialogue French system prompt for the chosen register. The REGISTER
    block swaps by `dtype`; everything else (TTS-safe forms, unscripted feel, content
    rules) is shared across all three registers."""
    reg = _NATURAL_REGISTERS.get(dtype, _NATURAL_REGISTERS["conversational"])
    return f"""You are a French content generator that writes AUTHENTIC SPOKEN French dialogue —
{reg['intro']}.

Return ONLY valid JSON with this exact structure — no markdown, no explanation, just JSON:
{{
  "title": "short French title for the scene",
  "lines": [
    {{ "speaker": "<first speaker name>", "text": "one turn of spoken French" }},
    {{ "speaker": "<second speaker name>", "text": "the reply" }}
  ],
  "vocab_preview": [
    {{ "word": "exact word/expression from the dialogue", "gloss": "brief French-only definition", "example": "the exact line containing it" }}
  ],
  "questions": [
    {{ "type": "literal", "question": "Question in French?", "options": ["A","B","C","D"], "correct_index": 0, "explanation": "Short French explanation." }}
  ]
}}

{reg['rules']}

TTS-SAFE SPOKEN FORMS (apply in every register):
- Use only the elisions the voice pronounces cleanly: "t'as", "t'es", "y a", "j'ai", "j'sais", "c'est", "qu'est-ce que", "j'vais", "i'faut".
- DO NOT write hard phonetic reductions that text-to-speech mispronounces — AVOID "chais pas", "chuis", "oué", "ché". Write "j'sais pas", "j'suis", "ouais" instead.

SOUND UNSCRIPTED, NOT LIKE A LESSON (this is what makes or breaks it):
- It's an overheard conversation, not a Q&A. Do NOT have one person cleanly ask and the other cleanly answer, turn after turn.
- Vary turn length a LOT: mix one- or two-word reactions ("Ah oui ?", "Mmh.", "Attends", "D'accord") with longer turns.
- Some turns should just react or agree and add nothing new — that's how real talk works.
- Let the topic DRIFT: they can wander onto a mutual acquaintance, a side story or a tangent, then loop back.
- Interruptions and finishing the other's thought are good.
- Ground it in concrete specifics — a real-sounding place, time, dish or named person — so it feels lived-in, not generic.
- Light emotional colour: mild complaining, surprise, a laugh ("haha") used sparingly (fit it to the register).
- Keep false starts LIGHT for the voice: at most an occasional self-correction with a comma ("enfin, je veux dire"). NEVER use "..." — the TTS reads it as a long dead pause.

CONTENT RULES:
- 2 speakers only, alternating. Use the two speaker NAMES given in the user message as the "speaker" label on every line, and refer to the speakers by those names in every question, option, and explanation — never "A"/"B" or "le premier locuteur".
- Calibrate richness to CEFR level (vocabulary breadth and idiom density), but the REGISTER above ALWAYS applies, at every level.
- A1: 5-6 very short, simple turns, one everyday situation. A2: 6-8 short turns, very common situations. B1: 8-12 turns. B2: 10-14 turns, opinions and nuance. C1/C2: 12-16 turns, implicit meaning and irony.
- vocab_preview: 4-6 of the most useful words/expressions actually used in the dialogue (prefer items that fit the register above).
- Questions: include one of each type in this order — "literal", "inference", "vocabulary", "main_idea"; quote the word for vocabulary questions; all French; plausible distractors that fail on close listening.
- Everything (lines, questions, options, explanations, glosses) in French."""


_NATURAL_Q_COUNT = {"A1": 3, "A2": 3, "B1": 4, "B2": 4, "C1": 5, "C2": 5}


async def _render_dialogue_lines(banked_lines: list) -> list:
    """Turn banked dialogue lines ({speaker, role, text, voice}) into playable lines
    with audio_url. Audio is content-addressed, so reusing a banked dialogue is a
    cache hit — no re-synthesis and no new Chirp spend."""
    out = []
    for ln in banked_lines:
        edge_voice = VOICE if ln.get("role") == "A" else VOICE_B
        audio_file = await generate_library_audio(ln["text"], ln["voice"], edge_voice=edge_voice)
        out.append({
            "speaker": ln["speaker"], "role": ln.get("role", "A"),
            "text": ln["text"], "audio_url": f"/audio/{audio_file}",
        })
    return out


@app.post("/natural/generate")
async def natural_generate(req: Request):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="Mistral not configured")

    data = await req.json()
    level = data.get("level", "B1")
    topic = data.get("topic", "la vie quotidienne")
    dtype = data.get("type", "conversational")  # register preset; also the bank style
    if dtype not in _NATURAL_REGISTERS:
        dtype = "conversational"
    speed = data.get("speed", "normal")  # playback preset, applied client-side; kept for analytics
    session_id = data.get("session_id")
    access_code = data.get("access_code")
    visit_id = data.get("visit_id")
    q_count = _NATURAL_Q_COUNT.get(level, 4)

    # Reuse an unseen banked dialogue first (register="casual", style=register preset);
    # generate + bank only on exhaustion. The banked lines carry their per-speaker
    # voice, so reuse replays from the audio cache for free.
    dlg = _bank_pick("passage", "casual", level, topic, dtype, access_code)
    if dlg is not None:
        out_lines = await _render_dialogue_lines(dlg.get("lines", []))
        _analytics.mark_bank_seen(access_code, dlg["id"], "dialogue")
        if session_id and access_code:
            _analytics.track(session_id, access_code, "natural_listen_started", {
                "exercise_type": "natural_listen", "level": level, "topic": topic,
                "type": dtype, "speed": speed, "question_count": len(dlg.get("questions", [])),
            }, visit_id)
        return {"title": dlg.get("title", ""), "lines": out_lines,
                "questions": dlg.get("questions", []), "vocab_preview": dlg.get("vocab_preview", [])}

    # Choose the voice pair up front so the two speakers can be named after their
    # voices — the names flow into both the dialogue lines and the questions.
    voice_a, voice_b = pick_dialogue_voices()
    name_a = voice_display_name(voice_a)  # e.g. "Julien"
    name_b = voice_display_name(voice_b)  # e.g. "Manon"

    user_prompt = (
        f"CEFR level: {level}\n"
        f"Topic / situation: {topic}\n"
        f"Number of questions: {q_count}\n"
        f"Speaker names: the two friends are named {name_a} and {name_b}. Use these "
        f"exact names as the \"speaker\" label on every line, and refer to them by "
        f"name in all questions, options and explanations.\n\n"
        "Write a natural spoken French dialogue between these two friends, plus vocab_preview and questions now."
    )

    resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _natural_system(dtype)},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        temperature=0.85,
        max_tokens=2400,
    ))

    result = json.loads(resp.choices[0].message.content)
    title = result.get("title", "")
    lines = result.get("lines", [])
    questions = result.get("questions", [])
    vocab_preview = result.get("vocab_preview", [])

    # Shuffle options to defeat the model's first-position correct-answer bias.
    for q in questions:
        opts = q.get("options")
        if not isinstance(opts, list) or len(opts) < 2:
            continue
        ci = q.get("correct_index", 0)
        if not isinstance(ci, int) or not (0 <= ci < len(opts)):
            ci = 0
        correct_opt = opts[ci]
        random.shuffle(opts)
        q["options"] = opts
        q["correct_index"] = opts.index(correct_opt)

    # Map each line to a stable role ("A"/"B") and its speaker's voice. The model
    # labels lines with the two names we supplied; fall back to strict alternation
    # if a label is off. Bank the lines (with voice) so the dialogue is reusable.
    banked_lines = []
    prev_role = "B"
    for ln in lines:
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        label = (ln.get("speaker") or "").strip().lower()
        if label == name_a.lower():
            role = "A"
        elif label == name_b.lower():
            role = "B"
        else:
            role = "A" if prev_role == "B" else "B"  # unknown label -> alternate
        prev_role = role
        banked_lines.append({
            "speaker": name_a if role == "A" else name_b, "role": role,
            "text": text, "voice": voice_a if role == "A" else voice_b,
        })

    out_lines = await _render_dialogue_lines(banked_lines)
    dlg_rec = content_bank.add_passage(
        "casual", level, topic, voice_a, [], style=dtype,
        questions=questions, vocab_preview=vocab_preview,
        payload={"title": title, "lines": banked_lines, "voices": [voice_a, voice_b]},
    )
    _analytics.mark_bank_seen(access_code, dlg_rec["id"], "dialogue")

    if session_id and access_code:
        _analytics.track(session_id, access_code, "natural_listen_started", {
            "exercise_type": "natural_listen",
            "level": level,
            "topic": topic,
            "type": dtype,
            "speed": speed,
            "question_count": len(questions),
        }, visit_id)

    return {"title": title, "lines": out_lines, "questions": questions, "vocab_preview": vocab_preview}


# ── Dictation routes ────────────────────────────────────────────────────────────

_DICTATION_SENTENCE_SYSTEM = """You generate French dictation sentences for language learners.
Generate exactly ONE natural French sentence calibrated to the given CEFR level and topic.

CEFR guidelines:
- A1: 5–8 words, present tense, basic common vocabulary
- A2: 8–12 words, passé composé or near future, everyday vocabulary
- B1: 10–16 words, varied tenses, compound sentences, some idiomatic phrases
- B2: 12–20 words, subordinate clauses, nuanced vocabulary, more complex grammar
- C1: 15–25 words, complex syntax, formal or colloquial register, idiomatic expressions
- C2: 20+ words, native-level complexity, varied register

Return ONLY the sentence — no quotes, no explanation, no extra punctuation."""

_dictation_sentences: dict = {}  # sentence_id → {sentence, level, topic}


class DictationGenerateRequest(BaseModel):
    level: str = "B1"
    topic: str = "la vie quotidienne"
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None


class DictationCheckRequest(BaseModel):
    sentence_id: str
    typed: str
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None
    attempt_number: Optional[int] = 1


@app.post("/dictation/generate")
async def dictation_generate(req: DictationGenerateRequest):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="Mistral not configured")

    # Serve a banked phrase from the shared pool (cross-pollinated with
    # paragraph/shadow) per the reuse policy; generate + bank a dictation-style
    # sentence when the policy calls for new content.
    rec = _bank_pick("phrase", "standard", req.level, req.topic, "", req.access_code)
    if rec is None:
        resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
            model=_MODEL,
            messages=[
                {"role": "system", "content": _DICTATION_SENTENCE_SYSTEM},
                {"role": "user", "content": f"CEFR level: {req.level}\nTopic: {req.topic}"},
            ],
            temperature=0.7,
            max_tokens=120,
        ))
        sentence = resp.choices[0].message.content.strip().strip('"').strip("'")
        rec = await _synth_and_bank_phrase(sentence, "standard", req.level, req.topic, "dictation", pick_narrator_voice())

    _analytics.mark_bank_seen(req.access_code, rec["id"], "dictation")
    sentence_id = uuid.uuid4().hex
    _dictation_sentences[sentence_id] = {"sentence": rec["text"], "level": req.level, "topic": req.topic}
    return {"sentence_id": sentence_id, "audio_url": f"/audio/{rec['audio_hash']}"}


@app.post("/dictation/check")
async def dictation_check(req: DictationCheckRequest):
    entry = _dictation_sentences.get(req.sentence_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Sentence not found")

    sentence = entry["sentence"]
    target_words = normalize(sentence)
    typed_words = normalize(req.typed)
    word_results = run_sequence_match(target_words, typed_words)
    display_results = build_display_results(sentence, word_results)

    matched = sum(1 for dr in display_results if dr["matched"])
    total = len(display_results)
    score = matched / total if total else 0.0

    if req.session_id and req.access_code:
        _analytics.track(req.session_id, req.access_code, "dictation_attempted", {
            "exercise_type": "dictation",
            "level": entry["level"],
            "topic": entry["topic"],
            "score": round(score, 3),
            "attempt_number": req.attempt_number,
            "word_results": [[dr["word"], dr["matched"], dr.get("said", "")] for dr in display_results],
        }, req.visit_id)

    mismatches = [
        {"target_word": dr["word"], "typed": dr["said"] or ""}
        for dr in display_results if not dr["matched"]
    ]
    feedback = await asyncio.to_thread(
        analyze_dictation_mismatches, sentence, req.typed, mismatches, _mistral
    )

    return {
        "sentence": sentence,
        "score": round(score, 3),
        "display_results": display_results,
        "feedback": feedback,
    }


class DictationCheckInlineRequest(BaseModel):
    target: str
    typed: str
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None
    level: Optional[str] = None
    topic: Optional[str] = None
    attempt_number: Optional[int] = 1


@app.post("/dictation/check-inline")
async def dictation_check_inline(req: DictationCheckInlineRequest):
    if not req.target.strip() or not req.typed.strip():
        raise HTTPException(status_code=400, detail="target and typed are required")
    target_words = normalize(req.target)
    typed_words  = normalize(req.typed)
    word_results = run_sequence_match(target_words, typed_words)
    display_results = build_display_results(req.target, word_results)
    matched = sum(1 for dr in display_results if dr["matched"])
    total   = len(display_results)
    score   = matched / total if total else 0.0

    if req.session_id and req.access_code:
        _analytics.track(req.session_id, req.access_code, "dictation_attempted", {
            "exercise_type": "dictation",
            "level": req.level,
            "topic": req.topic,
            "score": round(score, 3),
            "attempt_number": req.attempt_number,
            "word_results": [[dr["word"], dr["matched"], dr.get("said", "")] for dr in display_results],
        }, req.visit_id)

    mismatches = [
        {"target_word": dr["word"], "typed": dr["said"] or ""}
        for dr in display_results if not dr["matched"]
    ]
    feedback = await asyncio.to_thread(
        analyze_dictation_mismatches, req.target, req.typed, mismatches, _mistral
    )
    return {
        "sentence": req.target,
        "score": round(score, 3),
        "display_results": display_results,
        "feedback": feedback,
    }


# ── Vocabulary routes ───────────────────────────────────────────────────────────

class VocabCard(BaseModel):
    word: str
    part_of_speech: str
    usage: str
    french_definition: str
    english_definition: str
    example_sentence: str
    english_translation: str

class VocabGenerateRequest(BaseModel):
    level: str
    subject: str
    count: int = 8
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None

class VocabGenerateResponse(BaseModel):
    cards: list[VocabCard]

_VOCAB_SYSTEM = """You are a French language teacher generating vocabulary flashcards.

Generate exactly {count} vocabulary items (words, phrases, or idiomatic expressions) appropriate for a {level} learner on the subject: {subject}.

Rules:
- For A1/A2: use high-frequency words and simple phrases only
- For B1/B2: include common idioms, collocations, and phrasal verbs
- For C1/C2: include nuanced expressions, literary terms, register variation
- french_definition: a concise definition IN FRENCH, appropriate to the learner's level (simpler French for A1/A2)
- english_definition: an English translation of the french_definition (not the word itself — translate the definition)
- example_sentence: one natural sentence using the word/phrase in context
- part_of_speech: one of "verbe", "nom", "adjectif", "adverbe", "expression", "locution"
- usage: one of "courant", "familier", "soutenu"

Focus for this session: {angle}

Return ONLY valid JSON array, no other text:
[{{"word": "...", "part_of_speech": "...", "usage": "...", "french_definition": "...", "english_definition": "...", "example_sentence": "...", "english_translation": "..."}}, ...]"""

_VOCAB_ANGLES = [
    "Prioritise verbs and action words — what people do, feel, or experience.",
    "Prioritise concrete nouns — objects, places, and things people interact with.",
    "Prioritise adjectives and descriptive words — qualities and characteristics.",
    "Prioritise idiomatic expressions and set phrases used in natural speech.",
    "Prioritise formal or written register — words found in articles, official writing.",
    "Prioritise informal or conversational words — casual everyday speech.",
    "Prioritise abstract nouns — concepts, ideas, emotions, and states.",
    "Prioritise less obvious vocabulary — avoid the first words that come to mind for this topic.",
    "Prioritise collocations and multi-word expressions that native speakers use together.",
    "Prioritise words related to the senses — sight, sound, touch, taste, smell in this context.",
]

@app.post("/vocab/generate", response_model=VocabGenerateResponse)
async def vocab_generate(req: VocabGenerateRequest):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="API key not configured")
    level = req.level.upper()
    count = max(4, min(20, req.count))
    angle = random.choice(_VOCAB_ANGLES)
    system = _VOCAB_SYSTEM.format(count=count, level=level, subject=req.subject, angle=angle)
    try:
        raw = await asyncio.to_thread(
            lambda: _mistral.chat.complete(
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Generate {count} vocabulary cards."},
                ],
                temperature=1.0,
                max_tokens=3200,
            )
        )
        text = raw.choices[0].message.content.strip()
        # Strip markdown code fences if present
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        cards_raw = json.loads(text)
        cards = [VocabCard(**c) for c in cards_raw[:count]]
        if req.session_id and req.access_code:
            _analytics.track(req.session_id, req.access_code, "vocab_session_started", {
                "exercise_type": "vocab",
                "level": level,
                "subject": req.subject,
                "card_count": len(cards),
            }, req.visit_id)
        return VocabGenerateResponse(cards=cards)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")


# ── Resumable cumulative vocab session (tied to the logged-in account) ──────────

class VocabSessionSave(BaseModel):
    payload: dict


def _current_user_id(current_user: dict) -> int:
    try:
        return int(current_user.get("sub"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid account")


@app.get("/vocab/session")
async def vocab_session_get(current_user: dict = Depends(_auth.get_current_user)):
    return {"session": _analytics.get_vocab_session(_current_user_id(current_user))}


@app.put("/vocab/session")
async def vocab_session_save(req: VocabSessionSave, current_user: dict = Depends(_auth.get_current_user)):
    _analytics.save_vocab_session(_current_user_id(current_user), req.payload)
    return {"ok": True}


@app.delete("/vocab/session")
async def vocab_session_delete(current_user: dict = Depends(_auth.get_current_user)):
    _analytics.delete_vocab_session(_current_user_id(current_user))
    return {"ok": True}


_WRITING_PROMPT_SYSTEM = """You generate French writing prompts for language learners.
Generate a brief, clear writing task in French that asks the learner to write 1-2 sentences in French.
The task must be natural, engaging, and calibrated to the CEFR level and topic.

CEFR guidelines:
- A1: Simple personal questions (name, age, likes, family, colours, numbers)
- A2: Describe familiar objects, places, or recent events
- B1: Express an opinion or preference with a simple reason
- B2: Compare two things, argue a position, or describe a cause and effect
- C1: Nuanced argument, formal register, idiomatic expression challenge

Vary the task type naturally: description, opinion, question-answer, short narrative, or sentence completion.
Return ONLY the French prompt text — no quotes, no English, no explanation."""

_WRITING_CHECK_SYSTEM = """You are a supportive French writing coach. Your feedback is delivered in LAYERS so the learner can struggle productively first, then reveal more help only if they need it. You always provide every layer — the app decides what to show and when. The learner must never end up stranded without an answer.

The learner was asked (in French): {prompt}
They wrote: {response}
Their CEFR level: {level}

Return a JSON object with exactly these fields:
{{
  "has_errors": true or false,
  "overall": "one or two warm sentences in English: name something genuine they did well, then frame the practice positively",
  "corrections": [
    {{
      "excerpt": "the exact word or short phrase copied verbatim from THEIR text that needs work",
      "category": "one of: agreement, tense, conjugation, gender, article, preposition, word-choice, spelling, word-order, register",
      "nudge": "LAYER 1 — locate the issue and ask a guiding question. Name the area but DO NOT reveal the answer.",
      "example": "LAYER 2 — teach the rule with a SHORT worked example that uses DIFFERENT words than theirs, so the pattern transfers. One or two sentences. Still do not give their answer.",
      "fix": "LAYER 3 — the corrected version of their excerpt only (just the fixed word or phrase)",
      "why": "LAYER 3 — one plain-English sentence explaining the rule behind the fix"
    }}
  ],
  "corrected_sentence": "the learner's full text rewritten correctly, preserving their meaning and keeping as much of their original wording as possible — fix only what is wrong",
  "model_answer": "a natural, native-like example answer to the same prompt at the learner's level — a model to learn from, NOT a copy of their text"
}}

Guidelines:
- Be encouraging and concrete. Always find something genuine to praise in "overall".
- Give AT MOST 3 corrections — the highest-value ones first (meaning-blocking errors, then accuracy, then style). Ignore trivial slips when there are bigger issues.
- "nudge" must NOT reveal the correction. "example" must use vocabulary different from the learner's sentence. The corrected form appears ONLY in "fix".
- Calibrate rule vocabulary to the level:
  A1/A2 — plain English rule names, maximum scaffolding (e.g. "past tense with être", "noun–adjective agreement").
  B1 — French grammatical terms alongside English (passé composé, accord du participe passé, pronom COD).
  B2 — French grammatical terms (subjonctif, concordance des temps, gérondif); also flag word choice and register.
  C1 — technical terms, concise (subjonctif passé, nominalisation, euphonie).
- If has_errors is false: "corrections" is an empty array [], "corrected_sentence" equals their text, and "model_answer" still offers a strong alternative phrasing to learn from.
Return ONLY the raw JSON object, no markdown fences, no extra text."""


class WritingPromptRequest(BaseModel):
    level: str = "B1"
    topic: str = "la vie quotidienne"


class WritingCheckRequest(BaseModel):
    prompt: str
    response: str
    level: str = "B1"
    attempt: int = 1
    topic: Optional[str] = None
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None


@app.post("/writing/prompt")
async def writing_prompt(req: WritingPromptRequest):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="Mistral not configured")
    resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
        model="mistral-small-latest",
        messages=[
            {"role": "system", "content": _WRITING_PROMPT_SYSTEM},
            {"role": "user", "content": f"CEFR level: {req.level}\nTopic: {req.topic}"},
        ],
        temperature=0.9,
        max_tokens=120,
    ))
    prompt_text = resp.choices[0].message.content.strip().strip('"').strip("'")
    return {"prompt": prompt_text}


@app.post("/writing/check")
async def writing_check(req: WritingCheckRequest):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="Mistral not configured")
    if not req.prompt.strip() or not req.response.strip():
        raise HTTPException(status_code=400, detail="prompt and response are required")

    system = _WRITING_CHECK_SYSTEM.format(
        prompt=req.prompt,
        response=req.response,
        level=req.level,
    )
    resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": "Provide feedback on my writing."},
        ],
        temperature=0.3,
        max_tokens=1300,
    ))
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        result = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=500, detail="Feedback parse error")
    if req.session_id and req.access_code:
        has_errors = result.get("has_errors", True)
        correction_count = len(result.get("corrections", []))
        score = 1.0 if not has_errors else (0.5 if req.attempt > 1 else 0.0)
        _analytics.track(req.session_id, req.access_code, "writing_attempted", {
            "exercise_type": "writing",
            "level": req.level,
            "topic": req.topic,
            "attempt": req.attempt,
            "has_errors": has_errors,
            "score": round(score, 3),
            "correction_count": correction_count,
        }, req.visit_id)
    return result


_SPEAKING_PROMPT_SYSTEM = """You generate French speaking prompts for language learners practising free oral production.
Generate a brief, clear, inviting prompt in French that asks the learner to SPEAK aloud for 20-40 seconds.
The prompt must be natural, personal, and easy to talk about out loud — calibrated to the CEFR level and topic.

CEFR guidelines:
- A1: Very simple personal prompts (introduce yourself, your family, what you like)
- A2: Describe a daily routine, a place you know, or what you did recently
- B1: Give an opinion or tell a short story with a reason
- B2: Argue a position, compare two things, or describe an experience in detail
- C1: Nuanced reflection, a hypothetical situation, or an abstract topic

Favour prompts that invite a spoken monologue ("Racontez…", "Décrivez…", "Que pensez-vous de…", "Expliquez…").
Keep it to one or two sentences. Return ONLY the French prompt text — no quotes, no English, no explanation."""

_SPEAKING_CHECK_SYSTEM = """You are a warm, encouraging French speaking coach. The learner spoke aloud in response to a prompt and their words were captured by speech-to-text, so IGNORE missing punctuation, capitalisation, accents, and obvious transcription glitches — judge only the French they evidently produced.

The learner was asked (in French): {prompt}
They said (speech-to-text transcript): {response}
Their CEFR level: {level}

Your goal is to TEACH and BUILD CONFIDENCE. Unlike a strict grader, you SHOW the better version and explain why — briefly and kindly.

Return a JSON object with exactly these fields:
{{
  "overall": "2-3 warm sentences in English: name what they managed to communicate, then frame the next step positively",
  "strengths": ["1-3 specific, concrete things they did well — a correct structure, a good word choice, a clear idea (English)"],
  "corrections": [
    {{
      "said": "the phrase as they said it (French)",
      "better": "a natural, correct French version",
      "why": "one short, plain-English reason the learner can act on — name the rule simply"
    }}
  ],
  "level_up": "optional: one French word or expression that would make their answer sound more natural, with a short English gloss in parentheses. Empty string if nothing to add."
}}

Rules:
- Be supportive first. ALWAYS find at least one genuine strength.
- Give AT MOST 3 corrections — only the highest-value ones, never every small slip. If the French is essentially correct, return an empty corrections array and celebrate that in "overall".
- Corrections SHOW the fix (this is teaching, not testing). Keep each "why" to one sentence, calibrated to {level}.
- Never be harsh or discouraging. Do not assign scores or grades.
Return ONLY the raw JSON object, no markdown fences, no extra text."""


class SpeakingPromptRequest(BaseModel):
    level: str = "B1"
    topic: str = "la vie quotidienne"


class SpeakingCheckRequest(BaseModel):
    prompt: str
    response: str
    level: str = "B1"
    topic: Optional[str] = None
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None


@app.post("/speaking/prompt")
async def speaking_prompt(req: SpeakingPromptRequest):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="Mistral not configured")
    resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
        model="mistral-small-latest",
        messages=[
            {"role": "system", "content": _SPEAKING_PROMPT_SYSTEM},
            {"role": "user", "content": f"CEFR level: {req.level}\nTopic: {req.topic}"},
        ],
        temperature=0.9,
        max_tokens=120,
    ))
    prompt_text = resp.choices[0].message.content.strip().strip('"').strip("'")
    return {"prompt": prompt_text}


@app.post("/speaking/check")
async def speaking_check(req: SpeakingCheckRequest):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="Mistral not configured")
    if not req.prompt.strip() or not req.response.strip():
        raise HTTPException(status_code=400, detail="prompt and response are required")

    system = _SPEAKING_CHECK_SYSTEM.format(
        prompt=req.prompt,
        response=req.response,
        level=req.level,
    )
    resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
        model=_MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": "Give me supportive feedback on what I said."},
        ],
        temperature=0.4,
        max_tokens=900,
    ))
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        result = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=500, detail="Feedback parse error")
    if req.session_id and req.access_code:
        corrections = result.get("corrections", []) or []
        word_count = len(req.response.split())
        _analytics.track(req.session_id, req.access_code, "speaking_attempted", {
            "exercise_type": "speaking",
            "level": req.level,
            "topic": req.topic,
            "word_count": word_count,
            "correction_count": len(corrections),
        }, req.visit_id)
    return result


_TRANSFORM_GENERATE_SYSTEM = """You generate French sentence transformation exercises for language learners.

Return a JSON object with exactly two fields:
{
  "source": "the original French sentence",
  "instruction": "a brief instruction in French telling the learner what transformation to apply"
}

CEFR guidelines:
- A1/A2: Present tense, basic negation, singular→plural agreement
- B1: Passé composé ↔ imparfait, futur simple, direct object pronouns (le/la/les/lui/leur)
- B2: Conditionnel présent/passé, subjonctif présent, gérondif, multiple pronouns
- C1: Subjonctif passé, concordance des temps, nominalisation, style indirect

Focus types and examples:
- tense: change the tense of the main verb (name the target tense in the instruction)
- negation: make an affirmative sentence negative, or a negative sentence affirmative
- pronoun: replace a noun subject or object with the correct pronoun
- register: convert between tu/vous, or informal→formal vocabulary
- number: change singular to plural (or plural to singular), adjusting all agreements

CRITICAL — the source must NOT already satisfy the instruction. There has to be real work to do:
- tense: the source must be in a DIFFERENT tense than the target named in the instruction. If the instruction says "au passé composé", the source must be present/futur/imparfait — never already passé composé. Also make sure the source's time markers are consistent with the target after transforming (e.g. don't pair "demain" with a request for passé composé).
- negation: the source must already be in the opposite polarity (affirmative if you ask for negative, and vice versa).
- pronoun: the source must still contain the full noun that needs replacing.
- register: the source must be in the opposite register (tu if you ask for vous, and vice versa).
- number: the source must be in the opposite number (singular if you ask for plural, and vice versa).

Write the instruction in French as a direct command. One sentence only.
Keep the source sentence natural and calibrated to the CEFR level.
Return ONLY the raw JSON object, no markdown fences, no extra text."""

_TRANSFORM_CHECK_SYSTEM = """You are a French grammar coach evaluating a sentence transformation exercise.

Source sentence: {source}
Transformation instruction: {instruction}
Learner's attempt: {response}
CEFR level: {level}
Attempt number: {attempt} of 3

Return a JSON object with exactly these fields:
{{
  "has_errors": true or false,
  "tips": [
    "A pedagogical hint calibrated to the learner's level"
  ],
  "overall": "one sentence in English: honest and encouraging"
}}

Before judging, silently work out the correct transformed sentence yourself. Then compare
the learner's attempt against your own correct version. Evaluate:
1. Did they apply the requested transformation correctly?
2. Are there grammar errors in their version (agreement, conjugation, etc.)?
3. Did they preserve the meaning and structure of the rest of the sentence?

If the learner's attempt matches a valid correct transformation, set has_errors to false —
even if their wording differs from yours, as long as it is grammatically correct and applies
the requested change. Do NOT invent errors. Adjusting time markers to stay logical
(e.g. "hier"→"demain" when moving to a future tense) is correct, not an error, as long as
the requested transformation itself is applied. Never claim a form is wrong while also stating
that same form is the right answer — re-check before you flag anything.

NEVER give the correct form directly. Guide with hints.
Use level-appropriate rule vocabulary:

A1/A2 — Plain English rule names, maximum scaffolding, name the exact word and state the rule plainly.
B1 — Introduce French grammatical terms alongside English: "passé composé", "accord du participe passé", "pronom COD".
B2 — French grammatical terms only: "subjonctif", "conditionnel passé", "concordance des temps". Targeted nudge.
C1 — Technical terms only, one-line prompt.

Attempt progression:
- Attempt 1: name the rule and point to the area
- Attempt 2+: zoom in precisely on the same issue if still wrong

Maximum 3 tips. If has_errors is false, tips must be [].
Return ONLY the raw JSON object, no markdown fences, no extra text."""


class TransformGenerateRequest(BaseModel):
    level: str = "B1"
    focus: str = "tense"


class TransformCheckRequest(BaseModel):
    source: str
    instruction: str
    response: str
    level: str = "B1"
    attempt: int = 1
    focus: Optional[str] = None
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None


@app.post("/transform/generate")
async def transform_generate(req: TransformGenerateRequest):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="Mistral not configured")
    resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
        model="mistral-small-latest",
        messages=[
            {"role": "system", "content": _TRANSFORM_GENERATE_SYSTEM},
            {"role": "user", "content": f"CEFR level: {req.level}\nFocus: {req.focus}"},
        ],
        temperature=0.9,
        max_tokens=200,
    ))
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        result = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=500, detail="Generation parse error")
    return result


@app.post("/transform/check")
async def transform_check(req: TransformCheckRequest):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="Mistral not configured")
    if not req.source.strip() or not req.response.strip():
        raise HTTPException(status_code=400, detail="source and response are required")

    system = _TRANSFORM_CHECK_SYSTEM.format(
        source=req.source,
        instruction=req.instruction,
        response=req.response,
        level=req.level,
        attempt=req.attempt,
    )
    resp = await asyncio.to_thread(lambda: _mistral.chat.complete(
        model="mistral-small-latest",
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": "Evaluate my transformation."},
        ],
        temperature=0.3,
        max_tokens=800,
    ))
    raw = resp.choices[0].message.content.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        result = json.loads(raw)
    except Exception:
        raise HTTPException(status_code=500, detail="Feedback parse error")
    if req.session_id and req.access_code:
        has_errors = result.get("has_errors", True)
        tip_count = len(result.get("tips", []))
        score = 1.0 if not has_errors else (0.5 if req.attempt > 1 else 0.0)
        _analytics.track(req.session_id, req.access_code, "transform_attempted", {
            "exercise_type": "transform",
            "level": req.level,
            "focus": req.focus,
            "attempt": req.attempt,
            "has_errors": has_errors,
            "score": round(score, 3),
            "tip_count": tip_count,
        }, req.visit_id)
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
