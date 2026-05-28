import os
import re
import json
import time
from collections import deque
from mistralai import Mistral
from dotenv import load_dotenv
from shadow_engine import score_attempt
from pos_tagger import tag_nouns_adjs

load_dotenv()

_client = Mistral(api_key=os.environ.get("MISTRAL_API_KEY", "unset"))

SOUND_TARGETS = {
    "liaison": {
        "label": "Liaison",
        "desc": "Sounds that link across word boundaries",
        "focus": "many mandatory liaison opportunities: les‿enfants, vous‿avez, mes‿amis, ils‿ont, un‿ami, en‿hiver",
    },
    "nasal": {
        "label": "Nasal Vowels",
        "desc": "/ɑ̃/ /ɛ̃/ /ɔ̃/ — an, in, on",
        "focus": "words with nasal vowels: an/en /ɑ̃/ as in 'dans', 'enfant'; in/ein /ɛ̃/ as in 'pain', 'fin'; on /ɔ̃/ as in 'bon', 'monde'",
    },
    "u_vowel": {
        "label": "French /y/ Sound",
        "desc": "The tight-lipped u as in lune, tu, vu",
        "focus": "multiple words with the French /y/ vowel (lips tight and forward, not 'ou'): tu, vu, lune, une, sur, pur, du, plus",
    },
    "r_sound": {
        "label": "Uvular R /ʁ/",
        "desc": "The French throat R",
        "focus": "many words with the uvular /ʁ/ sound: regarder, vraiment, arriver, merci, partir, parler, trouver, prendre",
    },
    "open_vowels": {
        "label": "Open vs Closed",
        "desc": "é /e/ vs è /ɛ/ distinction",
        "focus": "contrast between closed /e/ (é, -er, -ez endings) and open /ɛ/ (è, ê, ai, ei): j'ai été, la fête, il aimait, les pieds, après",
    },
    "rhythm": {
        "label": "Rhythm & Flow",
        "desc": "Groupes rythmiques — French phrase stress",
        "focus": "3–4 clear rhythm groups of 2–4 words each, with natural enchaînement linking groups together",
    },
}

_PHRASE_SYSTEM = """You generate French phrases for prosody shadowing exercises.

The student focuses on SOUND and RHYTHM, not word recognition. Generate a phrase that strongly
exemplifies the requested sound focus, then annotate it for prosody display.

Return ONLY valid JSON — no markdown, no extra text — in this exact shape:
{
  "phrase": "Les enfants jouent dans le jardin",
  "ipa": "le.z‿ɑ̃.fɑ̃ ʒu | dɑ̃ lə ʒaʁ.dɛ̃",
  "syllabified": [
    {"word": "Les", "syllables": ["le"]},
    {"word": "enfants", "syllables": ["ɑ̃", "fɑ̃"]},
    {"word": "jouent", "syllables": ["ʒu"]},
    {"word": "dans", "syllables": ["dɑ̃"]},
    {"word": "le", "syllables": ["lə"]},
    {"word": "jardin", "syllables": ["ʒaʁ", "dɛ̃"]}
  ],
  "rhythm_groups": [["Les", "enfants", "jouent"], ["dans", "le", "jardin"]],
  "liaisons": [
    {"from_word": "Les", "to_word": "enfants", "sound": "z"}
  ],
  "enchaînements": [
    {"from_word": "cette", "to_word": "année", "sound": "t"}
  ]
}

STRICT RULES:
- syllabified: list EVERY word of the phrase in order, exactly as written in "phrase". Syllables must be PHONETIC (IPA) — show what the learner hears and says, not the spelling. Use the same IPA symbols as in the "ipa" field.
- rhythm_groups: arrays of word strings covering every word exactly once, in phrase order
- liaisons: only mandatory liaisons (les/des/mes/ses/ces/nos/vos/leurs + vowel-start word; vous/nous/ils/elles/on + vowel-start verb; adjective ending in consonant directly before vowel-start noun)
- enchaînements: only when a word ends in a normally-pronounced consonant that links into the next vowel (not liaison)
- IPA: broad transcription, use | to mark rhythm group boundaries, use ‿ for liaison
- Length by CEFR: A1=4–5 words, A2=5–7, B1=7–10, B2=10–13, C1=13–16, C2=16+
- The phrase must be something a native French speaker would naturally say"""

_ANALYSIS_SYSTEM = """You analyze French prosody shadowing results with a phonetic focus.

The student repeated a French phrase focusing on its SOUND and PROSODY.

You receive:
- target phrase (what they should have said)
- transcription (what speech recognition captured)
- mismatches (target word vs what was heard)
- sound_target (the phonetic feature being practiced)

For each mismatch write a tip in this exact format:
"<target_word> /<IPA>/ — <articulation cue>"

The cue must address the specific articulation failure as it relates to the sound target.
Examples:
  "enfants /ɑ̃.fɑ̃/ — nasal air through the nose, mouth stays open, no final consonant sound"
  "voudrais /vu.dʁɛ/ — lips rounded and forward for 'ou', then uvular R at back of throat"
  "été /e.te/ — lips spread in a tight smile for the closed /e/, tongue high and front"
Max 20 words total per tip.

Return ONLY valid JSON:
{
  "feedback": [
    {
      "target_word": "enfants",
      "said": "enfan",
      "tip": "enfants /ɑ̃.fɑ̃/ — nasal air through nose on both syllables, mouth open, no final consonant",
      "is_grammar": false,
      "grammar_note": ""
    }
  ]
}

If no mismatches: {"feedback": []}"""

_recent: dict = {}
_RECENT_MAX = 15


def generate_prosody_phrase(sound_target: str, level: str = "B1") -> dict:
    """Generate a phrase fully annotated for prosody display."""
    target_info = SOUND_TARGETS.get(sound_target, SOUND_TARGETS["liaison"])
    focus = target_info["focus"]

    key = (sound_target, level)
    recent = _recent.get(key, deque())
    avoid = ""
    if recent:
        listed = "; ".join(f'"{p}"' for p in recent)
        avoid = f" Do NOT reuse any of these recently generated phrases: {listed}."

    for attempt in range(3):
        try:
            resp = _client.chat.complete(
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": _PHRASE_SYSTEM},
                    {"role": "user", "content": f"Sound focus: {focus}\nCEFR level: {level}{avoid}"},
                ],
                temperature=0.85,
                max_tokens=1200,
            )
            raw = _strip_md_fence(resp.choices[0].message.content)
            data = json.loads(raw)
            phrase = data["phrase"]
            data["noun_adj_tokens"] = tag_nouns_adjs(phrase)

            if key not in _recent:
                _recent[key] = deque(maxlen=_RECENT_MAX)
            _recent[key].append(phrase)
            return data
        except Exception as e:
            if attempt < 2 and "429" in str(e):
                time.sleep(2 ** attempt)
                continue
            raise


def analyze_prosody_mismatches(
    target: str, transcription: str, mismatches: list, sound_target: str
) -> list:
    """Return phonetically-focused feedback for each mismatch."""
    if not mismatches:
        return []

    focus_label = SOUND_TARGETS.get(sound_target, {}).get("label", sound_target)
    payload = (
        f"Target phrase: {target}\n"
        f"Student said: {transcription}\n"
        f"Sound target being practiced: {focus_label}\n"
        f"Mismatches: {json.dumps(mismatches)}"
    )
    try:
        raw = _client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": _ANALYSIS_SYSTEM},
                {"role": "user", "content": payload},
            ],
            temperature=0.0,
            max_tokens=500,
        ).choices[0].message.content.strip()
        if not raw:
            return _fallback(mismatches)
        raw = _strip_md_fence(raw)
        return json.loads(raw).get("feedback", [])
    except Exception:
        return _fallback(mismatches)


_ANNOTATE_SYSTEM = """You annotate an existing French phrase for prosody/rhythm display.

Return ONLY valid JSON — no markdown, no extra text — in this exact shape:
{
  "ipa": "ʒə vu.dʁɛ | a.le | o maʁ.ʃe",
  "syllabified": [
    {"word": "Je", "syllables": ["Je"]},
    {"word": "voudrais", "syllables": ["vou", "drais"]}
  ],
  "rhythm_groups": [["Je", "voudrais"], ["aller"], ["au", "marché"]],
  "liaisons": [
    {"from_word": "Les", "to_word": "enfants", "sound": "z"}
  ]
}

STRICT RULES:
- syllabified: every word of the phrase in order, exactly as written
- rhythm_groups: arrays of word strings covering every word exactly once, in phrase order
- liaisons: only mandatory liaisons (det/pronoun ending in consonant + vowel-start word)
- IPA: broad transcription, | for rhythm group boundaries, ‿ for liaison"""


def _strip_md_fence(raw: str) -> str:
    """Strip markdown code fences from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    return raw.strip()


def annotate_phrase_rhythm(phrase: str) -> dict:
    """Annotate an existing phrase with syllabification, rhythm groups, liaisons, and IPA."""
    for attempt in range(3):
        try:
            resp = _client.chat.complete(
                model="mistral-small-latest",
                messages=[
                    {"role": "system", "content": _ANNOTATE_SYSTEM},
                    {"role": "user", "content": f"Annotate this French phrase: {phrase}"},
                ],
                temperature=0.0,
                max_tokens=1600,
            )
            raw = _strip_md_fence(resp.choices[0].message.content)
            return json.loads(raw)
        except json.JSONDecodeError:
            if attempt < 2:
                continue
            raise
        except Exception as e:
            if attempt < 2 and "429" in str(e):
                time.sleep(2 ** attempt)
                continue
            raise


def _fallback(mismatches: list) -> list:
    return [
        {
            "target_word": m["target_word"],
            "said": m["said"],
            "tip": "",
            "is_grammar": False,
            "grammar_note": "",
        }
        for m in mismatches
    ]
