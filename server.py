import os
import re
import uuid
import json
import random
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
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
import router as _router_module
import practice_list as pl
import analytics as _analytics

load_dotenv()

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

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

AUDIO_DIR = Path(tempfile.gettempdir()) / "french_tutor_audio"
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


class ParagraphStartRequest(BaseModel):
    level: str = "A1"
    topic: Optional[str] = None


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
    content_type: str = "phrases"  # "phrases" | "paragraphs" | "stories"


class CustomStartRequest(BaseModel):
    text: str
    content_type: str = "paragraphs"


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
    payload: dict = {}

@app.post("/track")
async def track_event(req: TrackRequest):
    _analytics.track(req.session_id, req.access_code, req.event_type, req.payload)
    return {"ok": True}


@app.get("/analytics")
async def get_analytics(key: str = ""):
    if not _ANALYTICS_KEYS or key not in _ANALYTICS_KEYS:
        raise HTTPException(status_code=403, detail="Forbidden")
    return _analytics.get_analytics()


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


@app.get("/analytics/dashboard", response_class=HTMLResponse)
async def analytics_dashboard(key: str = ""):
    if not _ANALYTICS_KEYS or key not in _ANALYTICS_KEYS:
        raise HTTPException(status_code=403, detail="Forbidden")
    data = _analytics.get_analytics()
    word_accuracy = {code: _analytics.get_word_accuracy(code) for code in data}
    drill_breakdown = {code: _analytics.get_sentence_drill_breakdown(code) for code in data}

    def score_bar(score):
        if score is None:
            return '<span style="color:rgba(26,26,26,0.3)">—</span>'
        pct = int(score * 100)
        color = "#7A9393" if pct >= 70 else "#BD3E31" if pct < 40 else "#A0A060"
        return f'<div style="display:flex;align-items:center;gap:8px"><div style="width:80px;height:6px;background:#E8E8E8"><div style="width:{pct}px;max-width:80px;height:6px;background:{color}"></div></div><span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.72rem">{pct}%</span></div>'

    rows = ""
    for code, stats in sorted(data.items()):
        ps = stats.get("phrase_shadowing", {})
        pa = stats.get("paragraph_shadowing", {})
        phrase_levels = ps.get("by_level", {})
        para_levels = pa.get("by_level", {})

        level_rows = ""
        all_levels = sorted(set(list(phrase_levels.keys()) + list(para_levels.keys())))
        for lvl in all_levels:
            ph = phrase_levels.get(lvl, {})
            pr = para_levels.get(lvl, {})
            level_rows += f"""
            <tr style="border-top:1px solid #E8E8E8">
              <td style="padding:6px 12px;font-size:0.72rem;color:rgba(26,26,26,0.5);padding-left:32px">LEVEL {lvl}</td>
              <td style="padding:6px 12px;font-size:0.72rem">{ph.get('attempts','—')}</td>
              <td style="padding:6px 12px">{score_bar(ph.get('avg_score'))}</td>
              <td style="padding:6px 12px;font-size:0.72rem">{pr.get('chunk_attempts','—')}</td>
              <td style="padding:6px 12px">{score_bar(pr.get('avg_chunk_score'))}</td>
              <td style="padding:6px 12px;font-size:0.72rem">{pr.get('sentence_drills','—')}</td>
              <td style="padding:6px 12px">{score_bar(pr.get('avg_drill_score'))}</td>
            </tr>"""

        rows += f"""
        <tr style="border-top:2px solid #1A1A1A;background:#FAFAFA">
          <td style="padding:10px 12px;font-family:'IBM Plex Mono',monospace;font-size:0.8rem;font-weight:700;letter-spacing:0.08em">{code.upper()}</td>
          <td style="padding:10px 12px;font-size:0.8rem">{stats.get('sessions',0)}</td>
          <td style="padding:10px 12px;font-size:0.8rem">{stats.get('total_shadowing_minutes',0)} min</td>
          <td style="padding:10px 12px;font-size:0.8rem">{ps.get('total_attempts','—')}</td>
          <td></td>
          <td style="padding:10px 12px;font-size:0.8rem">{pa.get('paragraphs_started','—')}</td>
          <td></td>
        </tr>
        {level_rows}"""

    coach_sections = ""
    all_codes = sorted(set(list(word_accuracy.keys()) + list(drill_breakdown.keys())))
    for code in all_codes:
        words = word_accuracy.get(code, [])
        drills = drill_breakdown.get(code, [])
        if not words and not drills:
            continue

        # ── Word accuracy tab content ──────────────────────────────────────────
        if words:
            word_rows = ""
            for w in words:
                pct = int(w["accuracy"] * 100)
                color = "#BD3E31" if pct < 40 else "#A0A060" if pct < 70 else "#7A9393"
                bar = f'<div style="display:flex;align-items:center;gap:8px"><div style="width:80px;height:6px;background:#E8E8E8"><div style="width:{pct}px;max-width:80px;height:6px;background:{color}"></div></div><span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.72rem">{pct}%</span></div>'
                word_rows += f"""
                <tr data-attempts="{w['attempts']}" data-accuracy="{w['accuracy']}" style="border-top:1px solid #E8E8E8">
                  <td style="padding:8px 12px;font-family:'IBM Plex Mono',monospace;font-size:0.8rem;font-weight:500">{w['word']}</td>
                  <td style="padding:8px 12px;font-size:0.75rem;color:rgba(26,26,26,0.5)">{w['attempts']}</td>
                  <td style="padding:8px 12px">{bar}</td>
                </tr>"""
            word_tab_content = f"""
            <table>
              <thead><tr>
                <th>Word</th>
                <th data-col="attempts" data-dir="" onclick="sortStruggleTable(this)" style="cursor:pointer;user-select:none">Attempts <span class="sort-ind"></span></th>
                <th data-col="accuracy" data-dir="asc" onclick="sortStruggleTable(this)" style="cursor:pointer;user-select:none">Accuracy <span class="sort-ind">▲</span></th>
              </tr></thead>
              <tbody>{word_rows}</tbody>
            </table>"""
        else:
            word_tab_content = '<div class="empty">NO WORD DATA YET — min. 5 attempts required</div>'

        # ── Sentence drill tab content ─────────────────────────────────────────
        if drills:
            drill_rows = ""
            for d in drills:
                avg_att = f"{d['avg_attempts']:.1f}" if d["avg_attempts"] is not None else "—"
                status = '<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;letter-spacing:0.1em;padding:3px 7px;background:#BD3E31;color:white">STRUGGLING</span>' if d["struggling"] else '<span style="font-family:\'IBM Plex Mono\',monospace;font-size:0.6rem;letter-spacing:0.1em;padding:3px 7px;background:#7A9393;color:white">PROGRESSING</span>'
                drill_rows += f"""
                <tr style="border-top:1px solid #E8E8E8">
                  <td style="padding:8px 12px;font-family:'IBM Plex Mono',monospace;font-size:0.75rem">LEVEL {d['level']}</td>
                  <td style="padding:8px 12px;font-size:0.75rem;color:rgba(26,26,26,0.6)">{d['sentences_practiced']}</td>
                  <td style="padding:8px 12px;font-size:0.75rem;color:rgba(26,26,26,0.6)">{avg_att}</td>
                  <td style="padding:8px 12px">{score_bar(d['avg_score'])}</td>
                  <td style="padding:8px 12px">{status}</td>
                </tr>"""
            drill_tab_content = f"""
            <table>
              <thead><tr>
                <th>Level</th><th>Sentences Practiced</th><th>Avg Attempts</th><th>Avg Score</th><th>Status</th>
              </tr></thead>
              <tbody>{drill_rows}</tbody>
            </table>"""
        else:
            drill_tab_content = '<div class="empty">NO SENTENCE DRILL DATA YET</div>'

        coach_sections += f"""
        <div class="coach-card">
          <div class="coach-header">
            <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
              <span style="font-family:'IBM Plex Mono',monospace;font-size:0.6rem;letter-spacing:0.2em;color:rgba(26,26,26,0.4);text-transform:uppercase">COACHING</span>
              <span style="font-family:'IBM Plex Mono',monospace;font-size:0.8rem;font-weight:700;letter-spacing:0.08em">{code.upper()}</span>
            </div>
            <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap">
              <div class="tab-bar">
                <button class="tab-btn active" onclick="switchTab(this,'{code}-word')">WORD ACCURACY</button>
                <button class="tab-btn" onclick="switchTab(this,'{code}-drill')">SENTENCE DRILLS</button>
              </div>
              <div style="display:flex;gap:8px">
                <a href="/analytics/word-accuracy/download?key={key}&access_code={code}" class="action-btn">DOWNLOAD CSV</a>
                <button onclick="resetCode('{code}')" class="action-btn danger">RESET DATA</button>
              </div>
            </div>
          </div>
          <div id="{code}-word" class="tab-panel">{word_tab_content}</div>
          <div id="{code}-drill" class="tab-panel" style="display:none">{drill_tab_content}</div>
        </div>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>ANALYTICS — FRENCH TUTOR</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;700&family=IBM+Plex+Sans:wght@300;400;500&display=swap" rel="stylesheet">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: 'IBM Plex Sans', system-ui, sans-serif; background: #F2F2F2; color: #1A1A1A; min-height: 100vh; }}
    .header {{ background: #1A1A1A; padding: 28px 40px; display: flex; align-items: baseline; gap: 16px; }}
    .header-title {{ font-family: Impact, 'Arial Black', sans-serif; font-size: 1.6rem; letter-spacing: 0.1em; color: white; }}
    .header-sub {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.6rem; letter-spacing: 0.22em; color: rgba(255,255,255,0.35); text-transform: uppercase; }}
    .container {{ max-width: 1100px; margin: 40px auto; padding: 0 24px; }}
    .stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 2px; margin-bottom: 40px; }}
    .stat-card {{ background: white; padding: 20px 24px; border-top: 3px solid #7A9393; }}
    .stat-label {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.55rem; letter-spacing: 0.2em; color: rgba(26,26,26,0.4); text-transform: uppercase; margin-bottom: 8px; }}
    .stat-value {{ font-family: Impact, sans-serif; font-size: 2rem; letter-spacing: 0.05em; color: #1A1A1A; }}
    .section-label {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.6rem; letter-spacing: 0.2em; color: rgba(26,26,26,0.4); text-transform: uppercase; margin-bottom: 12px; }}
    table {{ width: 100%; border-collapse: collapse; background: white; }}
    th {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.58rem; letter-spacing: 0.15em; text-transform: uppercase; color: rgba(26,26,26,0.45); padding: 10px 12px; text-align: left; border-bottom: 2px solid #1A1A1A; background: white; }}
    td {{ padding: 8px 12px; vertical-align: middle; }}
    .empty {{ text-align: center; padding: 48px; color: rgba(26,26,26,0.3); font-family: 'IBM Plex Mono', monospace; font-size: 0.75rem; letter-spacing: 0.1em; }}
    .coach-card {{ background: white; margin-bottom: 24px; }}
    .coach-header {{ background: #FAFAFA; border-bottom: 2px solid #1A1A1A; padding: 14px 16px; display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; }}
    .tab-bar {{ display: flex; border: 1px solid #E8E8E8; }}
    .tab-btn {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.58rem; letter-spacing: 0.12em; text-transform: uppercase; padding: 7px 14px; background: transparent; border: none; cursor: pointer; color: rgba(26,26,26,0.4); border-bottom: 2px solid transparent; margin-bottom: -1px; }}
    .tab-btn.active {{ color: #1A1A1A; border-bottom: 2px solid #7A9393; }}
    .tab-panel {{ padding: 0; }}
    .action-btn {{ font-family: 'IBM Plex Mono', monospace; font-size: 0.6rem; letter-spacing: 0.12em; text-transform: uppercase; padding: 6px 12px; background: #7A9393; color: white; text-decoration: none; border: none; cursor: pointer; display: inline-block; }}
    .action-btn.danger {{ background: #BD3E31; }}
  </style>
  <script>
    async function resetCode(code) {{
      if (!confirm('Delete all analytics data for "' + code + '"? This cannot be undone.')) return;
      const res = await fetch('/analytics/reset?key={key}&access_code=' + code, {{method:'POST'}});
      if (res.ok) {{ location.reload(); }} else {{ alert('Reset failed'); }}
    }}
    function switchTab(btn, panelId) {{
      const card = btn.closest('.coach-card');
      card.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
      card.querySelectorAll('.tab-panel').forEach(p => p.style.display = 'none');
      btn.classList.add('active');
      document.getElementById(panelId).style.display = '';
    }}
    function sortStruggleTable(th) {{
      const col = th.dataset.col;
      const dir = th.dataset.dir === 'asc' ? 'desc' : 'asc';
      const table = th.closest('table');
      table.querySelectorAll('th[data-col]').forEach(h => {{
        h.dataset.dir = '';
        h.querySelector('.sort-ind').textContent = '';
      }});
      th.dataset.dir = dir;
      th.querySelector('.sort-ind').textContent = dir === 'asc' ? ' ▲' : ' ▼';
      const tbody = table.querySelector('tbody');
      Array.from(tbody.querySelectorAll('tr'))
        .sort((a, b) => {{
          const av = parseFloat(a.dataset[col]);
          const bv = parseFloat(b.dataset[col]);
          return dir === 'asc' ? av - bv : bv - av;
        }})
        .forEach(r => tbody.appendChild(r));
    }}
  </script>
</head>
<body>
  <div class="header">
    <span class="header-title">ANALYTICS</span>
    <span class="header-sub">French Tutor — User Activity</span>
  </div>
  <div class="container">
    <div class="stat-grid">
      <div class="stat-card">
        <div class="stat-label">Total Users</div>
        <div class="stat-value">{len(data)}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Total Sessions</div>
        <div class="stat-value">{sum(v.get('sessions',0) for v in data.values())}</div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Shadowing Time</div>
        <div class="stat-value">{sum(v.get('total_shadowing_minutes',0) for v in data.values())}<span style="font-family:'IBM Plex Mono',monospace;font-size:0.9rem;margin-left:4px">min</span></div>
      </div>
      <div class="stat-card">
        <div class="stat-label">Phrase Attempts</div>
        <div class="stat-value">{sum(v.get('phrase_shadowing',{}).get('total_attempts',0) for v in data.values())}</div>
      </div>
    </div>

    <div class="section-label">Per User</div>
    {'<table><thead><tr><th>Access Code</th><th>Sessions</th><th>Shadow Time</th><th>Phrase Attempts</th><th>Avg Phrase Score</th><th>Paragraphs</th><th>Avg Chunk Score</th></tr></thead><tbody>' + rows + '</tbody></table>' if data else '<div class="empty">NO DATA YET</div>'}

    {('<div style="margin-top:48px"><div class="section-label" style="margin-bottom:24px">Coaching Breakdown</div>' + coach_sections + '</div>') if coach_sections else ''}
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)


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
        data = await asyncio.to_thread(lambda: generate_phrase(req.level, req.topic))
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
            "level": req.level,
            "topic": req.topic,
            "score": result["score"],
            "attempt_number": req.attempt_number,
            "word_results": [[wr["word"], wr["matched"]] for wr in result["word_results"]],
        })
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


# ── Paragraph shadow routes ────────────────────────────────────────────────────

class ParagraphStartRequestWithSession(ParagraphStartRequest):
    session_id: Optional[str] = None
    access_code: Optional[str] = None

@app.post("/paragraph/start", response_model=ParagraphStartResponse)
async def paragraph_start(req: ParagraphStartRequestWithSession):
    topic = req.topic or random.choice(TOPICS)
    try:
        data = await asyncio.to_thread(lambda: generate_paragraph(req.level, topic))
        audio_file = await generate_audio(data["paragraph"])
        paragraph_id = str(uuid.uuid4())
        if req.session_id and req.access_code:
            _analytics.track(req.session_id, req.access_code, "paragraph_started", {
                "paragraph_id": paragraph_id,
                "level": req.level,
                "topic": topic,
            })
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
            _analytics.track(req.session_id, req.access_code, "sentence_drilled", {
                "paragraph_id": req.paragraph_id,
                "chunk_index": req.chunk_index,
                "sentence_index": req.sentence_index,
                "level": req.level,
                "score": result["score"],
                "attempt_number": req.attempt_number,
                "word_results": [[wr["word"], wr["matched"]] for wr in result["word_results"]],
            })
        else:
            _analytics.track(req.session_id, req.access_code, "chunk_attempted", {
                "paragraph_id": req.paragraph_id,
                "chunk_index": req.chunk_index,
                "chunk_size": req.chunk_size,
                "level": req.level,
                "score": result["score"],
                "attempt_number": req.attempt_number,
                "word_results": [[wr["word"], wr["matched"]] for wr in result["word_results"]],
            })
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
        ct = e.get("content_type", "paragraphs")
        if ct == "stories":
            e["story_count"] = len(e.get("stories", []))
        else:
            e["sentence_count"] = len(e.get("sentences", []))
    return {"entries": entries}


@app.post("/custom/save")
async def custom_save(req: CustomSaveRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text is required")
    valid_types = {"phrases", "paragraphs", "stories"}
    content_type = req.content_type if req.content_type in valid_types else "paragraphs"
    entries = _load_user_content()
    entry = {
        "id": str(uuid.uuid4()),
        "label": req.label.strip() or "Untitled",
        "text": req.text.strip(),
        "content_type": content_type,
        "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
    }
    if content_type == "phrases":
        entry["sentences"] = _split_phrases(req.text.strip())
    elif content_type == "stories":
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
    valid_types = {"phrases", "paragraphs", "stories"}
    content_type = req.content_type if req.content_type in valid_types else "paragraphs"
    if content_type == "phrases":
        sentences = _split_phrases(req.text.strip())
    else:
        sentences = _split_sentences(req.text.strip())
    if not sentences:
        raise HTTPException(status_code=400, detail="No content found in text")
    if content_type == "phrases":
        return {"sentences": sentences, "full_audio_url": None, "content_type": "phrases"}
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
