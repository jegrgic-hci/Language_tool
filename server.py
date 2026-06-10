import os
import re
import uuid
import json
import random
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import asyncio
import edge_tts
from mistralai import Mistral

BASE_DIR = Path(__file__).parent

from router import route
import tutor as _tutor_module
from tutor import get_response
from document_engine import UPLOADS_DIR
import shadow_engine as _shadow_module
from shadow_engine import generate_phrase, score_attempt, analyze_mismatches as analyze_shadow_mismatches
import paragraph_engine as _paragraph_module
from paragraph_engine import generate_paragraph, score_chunk, TOPICS, analyze_mismatches, analyze_patterns
from score_utils import normalize, run_sequence_match, build_display_results, analyze_dictation_mismatches
import router as _router_module
import practice_list as pl
import analytics as _analytics
import prosody_engine as _prosody_module
from prosody_engine import generate_prosody_phrase, analyze_prosody_mismatches, SOUND_TARGETS, annotate_phrase_rhythm

load_dotenv()

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

AUDIO_DIR = Path(tempfile.gettempdir()) / "vraifrench_audio"
AUDIO_DIR.mkdir(exist_ok=True)

# In-memory sessions keyed by session_id
sessions: dict[str, dict] = {}


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_session(session_id: str) -> dict:
    if session_id not in sessions:
        sessions[session_id] = {
            "history": [],
            "mode": "CHAT",
            "topic": "general conversation",
            "drill_state": None,
        }
    return sessions[session_id]


VOICE = "fr-FR-DeniseNeural"

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


def normalize_french_transcript(text: str) -> str:
    corrections = [
        (r'\bje ai\b', "j'ai"),
        (r'\btu as\b', "t'as"),
        (r'\bce est\b', "c'est"),
        (r'\bne est\b', "n'est"),
        (r'\bne ai\b', "n'ai"),
        (r'\bde (\w+)', r"d'\1"),
        (r'\bque il\b', "qu'il"),
        (r'\bque elle\b', "qu'elle"),
    ]
    for pattern, replacement in corrections:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


async def generate_audio(text: str) -> str:
    filename = f"{uuid.uuid4().hex}.mp3"
    await edge_tts.Communicate(clean_for_tts(text), VOICE).save(str(AUDIO_DIR / filename))
    return filename


def _ask_mistral(system: str, user: str, max_tokens: int = 100) -> str:
    if _mistral is None:
        raise HTTPException(status_code=503, detail="API key not configured")
    resp = _mistral.chat.complete(
        model=_MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.8,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


_COHERENCE_SYSTEM = """You are checking whether a French language learner's message makes sense in context.

Given the recent conversation and the student's latest message, decide if the message is coherent.

A message is INCOHERENT if:
- It contains garbled, invented, or misspelled words that don't exist in French or English
- It is grammatically broken to the point that the intended meaning is impossible to guess
- It appears to be a speech recognition error (e.g. random syllables, phonetic noise)
- Key words are so wrong that the sentence has no recoverable meaning

A message IS coherent even if:
- Grammar is imperfect but the meaning is clear
- It mixes French and English
- It is a short or incomplete sentence
- It uses the wrong word but the intent is obvious

Return ONLY valid JSON: {"coherent": true} or {"coherent": false, "clarification": "<short natural French question asking them to repeat or clarify — 1 sentence>"}"""

_PRONUNCIATION_ANALYSIS_SYSTEM = """You are a French pronunciation analyst helping a tutor give targeted feedback.

Speech recognition flagged this transcription as low-confidence — the student likely mispronounced one word, causing a garbled transcription.

Given the recent conversation and the transcription:
1. Identify the single word most likely to be a speech recognition error
2. Determine the correct French word the student most likely intended
3. Write one short, practical pronunciation tip for that word

Return ONLY valid JSON in this exact shape:
{"suspected_word": "<garbled transcription>", "likely_intended": "<correct French word>", "tip": "<word> /<IPA>/ — <one body-mechanics cue: lip/tongue/nasal, max 15 words total>"}

If the entire message is too garbled to recover any meaning, return: {"garbled": true}"""


async def analyze_pronunciation_error(transcription: str, history: list[dict]) -> Optional[dict]:
    """Sub-prompt step: identify likely mispronounced word and return analysis dict, or None."""
    recent = history[-4:] if len(history) >= 4 else history
    context_lines = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in recent)
    user_payload = (
        f"Recent conversation:\n{context_lines}\n\n"
        f"Low-confidence transcription: {transcription}"
    )
    if _mistral is None:
        return None
    try:
        raw = await asyncio.to_thread(
            lambda: _mistral.chat.complete(
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": _PRONUNCIATION_ANALYSIS_SYSTEM},
                    {"role": "user",   "content": user_payload},
                ],
                temperature=0.0,
                max_tokens=120,
            ).choices[0].message.content.strip()
        )
        return json.loads(raw)
    except Exception:
        return None


_COHERENCE_LOW_CONF_SYSTEM = """You are checking whether a French language learner's spoken message was transcribed correctly.

Speech recognition reported LOW CONFIDENCE on this transcription — the student likely mispronounced one word, causing it to be garbled.

Given the recent conversation and the (possibly garbled) transcription:
1. Identify the single word most likely to be a speech recognition error — the one that seems out of place, phonetically plausible as a mispronunciation, or breaks the sentence meaning
2. Ask the student to repeat that specific word in a short, natural French sentence

Return ONLY valid JSON: {"coherent": false, "clarification": "<natural French question naming the specific suspect word and asking them to repeat it — max 12 words>"}

Always return coherent: false. Always name the specific word."""


def check_coherence(message: str, history: list[dict], confidence: Optional[float] = None) -> Optional[str]:
    """Returns a French clarification string if the message is incoherent, else None."""
    low_confidence = confidence is not None and confidence < 0.65

    # Skip check for very short inputs unless speech confidence was low
    if len(message.split()) <= 2 and not low_confidence:
        return None

    recent = history[-6:] if len(history) >= 6 else history
    context_lines = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in recent)

    if low_confidence:
        system = _COHERENCE_LOW_CONF_SYSTEM
        user_payload = (
            f"Recent conversation:\n{context_lines}\n\n"
            f"Low-confidence transcription (confidence={confidence:.0%}): {message}"
        )
    else:
        system = _COHERENCE_SYSTEM
        user_payload = f"Recent conversation:\n{context_lines}\n\nStudent's latest message: {message}"

    if _mistral is None:
        return None
    try:
        raw = _mistral.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user_payload},
            ],
            temperature=0.0,
            max_tokens=80,
        ).choices[0].message.content.strip()

        data = json.loads(raw)
        if not data.get("coherent", True):
            return data.get("clarification", "Je n'ai pas bien compris — tu peux répéter ?")
    except Exception:
        pass

    return None


# ── Schemas ────────────────────────────────────────────────────────────────────

class ChatRequest(BaseModel):
    message: str
    session_id: str
    confidence: Optional[float] = None


class ChatResponse(BaseModel):
    reply: str
    audio_url: str
    mode: str
    topic: str
    drill_type: Optional[str] = None


class TTSRequest(BaseModel):
    text: str


class ShadowPhraseRequest(BaseModel):
    level: str = 'A1'
    topic: Optional[str] = None
    style: Optional[str] = 'story'
    sound_focus: Optional[str] = None
    focus_word: Optional[str] = None


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


class ProsodyPhraseRequest(BaseModel):
    sound_target: str = "liaison"
    level: str = "B1"


class SyllabifiedWord(BaseModel):
    word: str
    syllables: list


class LiaisonMark(BaseModel):
    from_word: str
    to_word: str
    sound: str = ""


class ProsodyPhraseResponse(BaseModel):
    phrase: str
    audio_url: str
    sound_target: str
    level: str
    ipa: str = ""
    syllabified: list = []
    rhythm_groups: list = []
    liaisons: list = []
    enchaînements: list = []
    noun_adj_tokens: list = []


class ProsodyAnalyzeRequest(BaseModel):
    target: str
    transcription: str
    sound_target: str = "liaison"
    noun_adj_tokens: Optional[list] = None
    confidence: Optional[float] = None
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    attempt_number: Optional[int] = None


class ProsodyAnalyzeResponse(BaseModel):
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
    full_audio_url: str
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
    return FileResponse(BASE_DIR / "static" / "index.html")


class AccessCodeRequest(BaseModel):
    code: str

@app.post("/validate-code")
async def validate_code(req: AccessCodeRequest):
    if not req.code.strip() or req.code.strip() not in _ACCESS_CODES:
        raise HTTPException(status_code=401, detail="Invalid access code")
    return {"ok": True}


class TrackRequest(BaseModel):
    session_id: str
    access_code: str
    event_type: str
    visit_id: Optional[str] = None
    payload: dict = {}

@app.post("/track")
async def track_event(req: TrackRequest):
    _analytics.track(req.session_id, req.access_code, req.event_type, req.payload, req.visit_id)
    return {"ok": True}


@app.get("/analytics")
async def get_analytics(key: str = ""):
    if not _ANALYTICS_KEYS or key not in _ANALYTICS_KEYS:
        raise HTTPException(status_code=403, detail="Forbidden")
    return _analytics.get_analytics()


@app.get("/analytics/sessions")
async def get_session_history(key: str = "", access_code: str = ""):
    if not _ANALYTICS_KEYS or key not in _ANALYTICS_KEYS:
        raise HTTPException(status_code=403, detail="Forbidden")
    if not access_code:
        raise HTTPException(status_code=400, detail="access_code required")
    return {"sessions": _analytics.get_session_history(access_code)}


@app.get("/analytics/word-accuracy/download")
async def download_word_accuracy(key: str = "", access_code: str = ""):
    if not _ANALYTICS_KEYS or key not in _ANALYTICS_KEYS:
        raise HTTPException(status_code=403, detail="Forbidden")
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
async def reset_analytics(key: str = "", access_code: str = ""):
    if not _ANALYTICS_KEYS or key not in _ANALYTICS_KEYS:
        raise HTTPException(status_code=403, detail="Forbidden")
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


def _require_analytics_key(key: str):
    if not _ANALYTICS_KEYS or key not in _ANALYTICS_KEYS:
        raise HTTPException(status_code=403, detail="Forbidden")


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
async def list_students(key: str = ""):
    _require_analytics_key(key)
    return {"students": _analytics.get_roster()}


@app.post("/analytics/students")
async def add_student(req: AddStudentRequest, key: str = ""):
    _require_analytics_key(key)
    return _analytics.add_student(
        req.name, req.email, req.lesson_days, req.lesson_time, req.notes,
    )


@app.put("/analytics/students/{access_code}")
async def update_student(access_code: str, req: UpdateStudentRequest, key: str = ""):
    _require_analytics_key(key)
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
    key = next(iter(_ANALYTICS_KEYS), "")
    return RedirectResponse(url=f"/analytics/dashboard?key={key}")


@app.get("/analytics/dashboard")
async def analytics_dashboard(key: str = ""):
    _require_analytics_key(key)
    return FileResponse(BASE_DIR / "static" / "analytics.html")


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    session = get_session(req.session_id)
    message = normalize_french_transcript(req.message)

    # ── Number drill: validate pending answer ──────────────────────────────────
    if session["drill_state"] and session["drill_state"]["type"] == "number":
        target = session["drill_state"]["target"]
        given = re.sub(r"[\s.,]", "", message)
        correct = str(target)
        session["drill_state"] = None

        if given == correct:
            reply = f"Correct ! C'était bien {target}. Bravo !"
        else:
            reply = f"Pas tout à fait — la réponse était {target}. Tu as dit : {message}."

        return ChatResponse(
            reply=reply,
            audio_url=f"/audio/{await generate_audio(reply)}",
            mode=session["mode"],
            topic=session["topic"],
        )

    # ── Route intent ───────────────────────────────────────────────────────────
    new_mode, new_topic = route(message)

    if new_mode != session["mode"]:
        session["mode"] = new_mode
        session["topic"] = new_topic
        session["history"] = []

    # ── Start number drill ─────────────────────────────────────────────────────
    if new_mode == "DRILL" and any(w in new_topic.lower() for w in ("number", "chiffre", "nombre")):
        target = random.randint(1, 9999)
        sentence = _ask_mistral(
            system=(
                f"Generate ONE natural French sentence that contains the number {target}. "
                "The number MUST appear as digits in your response. Return ONLY the sentence."
            ),
            user=f"Sentence with {target}",
        )
        session["drill_state"] = {"type": "number", "target": target}

        return ChatResponse(
            reply=sentence,
            audio_url=f"/audio/{await generate_audio(sentence)}",
            mode=new_mode,
            topic=new_topic,
            drill_type="number",
        )

    # ── Low-confidence speech: sub-prompt analysis → targeted pronunciation note ──
    pronunciation_note = ""
    if req.confidence is not None and req.confidence < 0.65:
        analysis = await analyze_pronunciation_error(message, session["history"])
        if analysis and not analysis.get("garbled"):
            suspected = analysis.get("suspected_word", "")
            intended  = analysis.get("likely_intended", "")
            tip       = analysis.get("tip", "")
            if suspected and intended:
                pronunciation_note = (
                    f"[Pronunciation note for this turn: The student's speech recognition had "
                    f"low confidence. The word \"{suspected}\" in their message likely should be "
                    f"\"{intended}\". {('Tip: ' + tip) if tip else ''} "
                    f"Gently acknowledge this specific word in your reply — e.g. "
                    f"\"Tu voulais dire '{intended}' ?\" — then continue naturally. Keep your response short.]"
                )
        elif analysis and analysis.get("garbled"):
            # Completely unrecoverable — fall back to generic coherence clarification
            clarification = "Je n'ai pas bien compris — tu peux répéter ?"
            return ChatResponse(
                reply=clarification,
                audio_url=f"/audio/{await generate_audio(clarification)}",
                mode=session["mode"],
                topic=session["topic"],
                drill_type="clarification",
            )
    else:
        # ── Normal coherence check for typed or high-confidence speech ────────────
        clarification = check_coherence(message, session["history"], req.confidence)
        if clarification:
            return ChatResponse(
                reply=clarification,
                audio_url=f"/audio/{await generate_audio(clarification)}",
                mode=session["mode"],
                topic=session["topic"],
                drill_type="clarification",
            )

    # ── Regular chat / scenario / RAG / connector drill ────────────────────────
    session["history"].append({"role": "user", "content": message})
    reply = get_response(
        session["history"],
        session_mode=session["mode"],
        topic=session["topic"],
        pronunciation_note=pronunciation_note,
    )
    session["history"].append({"role": "assistant", "content": reply})

    return ChatResponse(
        reply=reply,
        audio_url=f"/audio/{await generate_audio(reply)}",
        mode=session["mode"],
        topic=session["topic"],
        drill_type="connector" if new_mode == "DRILL" else None,
    )


@app.get("/audio/{filename}")
async def serve_audio(filename: str):
    if not re.fullmatch(r"[a-f0-9]{32}\.mp3", filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = AUDIO_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Audio not found")
    return FileResponse(str(path), media_type="audio/mpeg")


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


@app.delete("/session/{session_id}")
async def reset_session(session_id: str):
    sessions.pop(session_id, None)
    return {"status": "reset"}


# ── TTS route ──────────────────────────────────────────────────────────────────

@app.post("/tts")
async def tts_word(req: TTSRequest):
    if not req.text or not req.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    audio_url = f"/audio/{await generate_audio(req.text.strip())}"
    return {"audio_url": audio_url}


def _build_noun_adj_set(tokens):
    """Convert Mistral's noun_adj_tokens list into a set of base forms (without terminal -s)."""
    result = set()
    for t in (tokens or []):
        t_lower = t.lower()
        result.add(t_lower[:-1] if t_lower.endswith("s") else t_lower)
    return result


# ── Shadow routes ──────────────────────────────────────────────────────────────

@app.post("/shadow/phrase", response_model=ShadowPhraseResponse)
async def shadow_phrase(req: ShadowPhraseRequest):
    try:
        data = await asyncio.to_thread(lambda: generate_phrase(req.level, req.topic, req.style or 'story', req.sound_focus, req.focus_word))
        audio_url = f"/audio/{await generate_audio(data['phrase'])}"
        return ShadowPhraseResponse(
            phrase=data["phrase"],
            audio_url=audio_url,
            level=req.level,
            noun_adj_tokens=data.get("noun_adj_tokens", []),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Phrase generation failed: {e}")


@app.post("/shadow/analyze", response_model=ShadowAnalyzeResponse)
async def shadow_analyze(req: ShadowAnalyzeRequest):
    noun_adj_set = _build_noun_adj_set(req.noun_adj_tokens)
    result = score_attempt(req.target, req.transcription, noun_adj_set)
    if req.session_id and req.access_code:
        _analytics.track(req.session_id, req.access_code, "phrase_attempted", {
            "exercise_type": "phrase",
            "level": req.level,
            "topic": req.topic,
            "score": result["score"],
            "passed": result["passed"],
            "attempt_number": req.attempt_number,
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
    word_results = [
        WordResult(word=wr["word"], matched=wr["matched"], said=wr["said"])
        for wr in result["word_results"]
    ]
    display_results = [
        WordResult(word=dr["word"], matched=dr["matched"], said=dr["said"])
        for dr in result["display_results"]
    ]
    return ShadowAnalyzeResponse(
        score=result["score"],
        passed=result["passed"],
        feedback=feedback,
        word_results=word_results,
        display_results=display_results,
    )


class ShadowRhythmRequest(BaseModel):
    phrase: str


@app.post("/shadow/rhythm")
async def shadow_rhythm(req: ShadowRhythmRequest):
    try:
        data = await asyncio.to_thread(lambda: annotate_phrase_rhythm(req.phrase))
        return data
    except Exception as e:
        print(f"[shadow/rhythm] ERROR for phrase={req.phrase!r}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Rhythm annotation failed: {e}")


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


# ── Prosody routes ────────────────────────────────────────────────────────────

@app.get("/prosody/targets")
async def prosody_targets():
    return {"targets": [{"key": k, "label": v["label"], "desc": v["desc"]} for k, v in SOUND_TARGETS.items()]}


@app.post("/prosody/phrase", response_model=ProsodyPhraseResponse)
async def prosody_phrase(req: ProsodyPhraseRequest):
    try:
        data = await asyncio.to_thread(lambda: generate_prosody_phrase(req.sound_target, req.level))
        audio_url = f"/audio/{await generate_audio(data['phrase'])}"
        return ProsodyPhraseResponse(
            phrase=data["phrase"],
            audio_url=audio_url,
            sound_target=req.sound_target,
            level=req.level,
            ipa=data.get("ipa", ""),
            syllabified=data.get("syllabified", []),
            rhythm_groups=data.get("rhythm_groups", []),
            liaisons=data.get("liaisons", []),
            enchaînements=data.get("enchaînements", []),
            noun_adj_tokens=data.get("noun_adj_tokens", []),
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Prosody phrase generation failed: {e}")


@app.post("/prosody/analyze", response_model=ProsodyAnalyzeResponse)
async def prosody_analyze(req: ProsodyAnalyzeRequest):
    noun_adj_set = _build_noun_adj_set(req.noun_adj_tokens)
    result = score_attempt(req.target, req.transcription, noun_adj_set)
    feedback_raw = await asyncio.to_thread(
        lambda: analyze_prosody_mismatches(req.target, req.transcription, result["mismatches"], req.sound_target)
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
    return ProsodyAnalyzeResponse(
        score=result["score"],
        passed=result["passed"],
        feedback=feedback,
        word_results=word_results,
        display_results=display_results,
    )


# ── Paragraph shadow routes ────────────────────────────────────────────────────

class ParagraphStartRequestWithSession(ParagraphStartRequest):
    session_id: Optional[str] = None
    access_code: Optional[str] = None
    visit_id: Optional[str] = None

@app.post("/paragraph/start", response_model=ParagraphStartResponse)
async def paragraph_start(req: ParagraphStartRequestWithSession):
    topic = req.topic or random.choice(TOPICS)
    try:
        data = await asyncio.to_thread(lambda: generate_paragraph(req.level, topic, req.style or 'story'))
        audio_file = await generate_audio(data["paragraph"])
        paragraph_id = str(uuid.uuid4())
        if req.session_id and req.access_code:
            _analytics.track(req.session_id, req.access_code, "paragraph_started", {
                "exercise_type": "paragraph",
                "paragraph_id": paragraph_id,
                "level": req.level,
                "topic": topic,
                "sentence_count": len(data["sentences"]),
            }, req.visit_id)
        return ParagraphStartResponse(
            sentences=data["sentences"],
            full_audio_url=f"/audio/{audio_file}",
            level=data["level"],
            topic=topic,
            noun_adj_tokens=data.get("noun_adj_tokens", []),
            paragraph_id=paragraph_id,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Paragraph generation failed: {e}")


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
    audio_file = await generate_audio(req.text.strip())
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


# ── Comprehension routes ────────────────────────────────────────────────────────

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

@app.post("/comprehension/generate")
async def comprehension_generate(req: Request):
    data = await req.json()
    level = data.get("level", "B1")
    topic = data.get("topic", "la vie quotidienne")
    num_paragraphs = min(max(int(data.get("num_paragraphs", 2)), 1), 4)
    q_count = _COMPREHENSION_Q_COUNT.get(level, 4)

    user_prompt = (
        f"CEFR level: {level}\n"
        f"Topic: {topic}\n"
        f"Number of paragraphs: {num_paragraphs}\n"
        f"Number of questions: {q_count}\n\n"
        "Generate the passage, vocab_preview, and comprehension questions now."
    )

    resp = _mistral.chat.complete(
        model=_MODEL,
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

    audio_url = f"/audio/{await generate_audio(passage)}"
    return {"passage": passage, "audio_url": audio_url, "questions": questions, "vocab_preview": vocab_preview}


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

_dictation_sentences: dict = {}


class DictationGenerateRequest(BaseModel):
    level: str = "B1"
    topic: str = "la vie quotidienne"


class DictationCheckRequest(BaseModel):
    sentence_id: str
    typed: str


@app.post("/dictation/generate")
async def dictation_generate(req: DictationGenerateRequest):
    if _mistral is None:
        raise HTTPException(status_code=503, detail="Mistral not configured")

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

    sentence_id = uuid.uuid4().hex
    _dictation_sentences[sentence_id] = sentence
    audio_url = f"/audio/{await generate_audio(sentence)}"
    return {"sentence_id": sentence_id, "audio_url": audio_url}


@app.post("/dictation/check")
async def dictation_check(req: DictationCheckRequest):
    sentence = _dictation_sentences.get(req.sentence_id)
    if not sentence:
        raise HTTPException(status_code=404, detail="Sentence not found")

    target_words = normalize(sentence)
    typed_words = normalize(req.typed)
    word_results = run_sequence_match(target_words, typed_words)
    display_results = build_display_results(sentence, word_results)

    matched = sum(1 for dr in display_results if dr["matched"])
    total = len(display_results)
    score = matched / total if total else 0.0

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
        return VocabGenerateResponse(cards=cards)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
