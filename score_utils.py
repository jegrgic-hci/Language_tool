import re
import json
import difflib

import lang as _lang

# Strip all punctuation including apostrophes — elisions like j'ai and jai score identically
_PUNCT_RE = re.compile(r"[^\w\s]")


def analyze_dictation_mismatches(target: str, typed: str, mismatches: list, client,
                                 lang: str = "fr") -> list:
    """
    Call Mistral to get listening/grammar feedback for each dictation mismatch.
    client: a Mistral client instance.
    Returns list of feedback dicts.
    """
    if not mismatches:
        return []

    payload = (
        f"Target sentence: {target}\n"
        f"Student typed: {typed}\n"
        f"Mismatches: {json.dumps(mismatches)}"
    )
    fallback = [{"target_word": m["target_word"], "typed": m["typed"], "phonetic_note": "", "grammar_note": "", "error_type": "phonetic"} for m in mismatches]
    try:
        raw = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": _lang.get(lang).DICTATION_ANALYSIS_SYSTEM},
                {"role": "user", "content": payload},
            ],
            temperature=0.0,
            max_tokens=600,
        ).choices[0].message.content.strip()
        if not raw:
            return fallback
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json\n"):
                raw = raw[5:]
            raw = raw.rstrip()
        return json.loads(raw).get("feedback", [])
    except Exception:
        return fallback


def normalize(text: str, noun_adj_set=None, phonetic: bool = False, lang: str = "fr") -> list:
    """Lowercase, normalize apostrophes, apply the language's text rules (e.g. French
    elision contraction), strip punctuation, split, then apply its word rules.

    phonetic=True enables sound-based collapsing for SPEAKING exercises: homophonous
    verb endings (parlé/parlez/parler → é) are canonicalized so the STT's arbitrary
    spelling choice can't cause a false miss. Dictation leaves this off so spelling
    still counts.
    """
    profile = _lang.get(lang)
    t = text.lower()
    t = t.replace("’", "'").replace("‘", "'").replace("´", "'")
    t = profile.normalize_text(t)
    t = t.replace("-", " ").replace("_", " ").replace("‿", " ").replace("⁀", " ")
    t = _PUNCT_RE.sub("", t)
    words = [w for w in t.split() if w]
    words = profile.normalize_words(words, phonetic)
    if noun_adj_set:
        words = [profile.strip_plural(w, noun_adj_set) for w in words]
    return words


def build_display_results(target: str, word_results: list, noun_adj_set=None,
                          lang: str = "fr") -> list:
    """
    Align word_results (normalized tokens) back to the original surface tokens in target.
    Hyphenated words normalize to 2 tokens, so consumes len(norm_parts) entries per display word.
    Returns list of { word, matched, said } dicts.
    """
    display_results = []
    ni = 0
    for orig_token in target.split():
        norm_parts = normalize(orig_token, noun_adj_set, lang=lang)
        if not norm_parts:
            continue
        wrs = word_results[ni:ni + len(norm_parts)]
        ni += len(norm_parts)
        matched = all(wr["matched"] for wr in wrs)
        said = " ".join(wr["said"] for wr in wrs if wr["said"])
        display_results.append({"word": orig_token, "matched": matched, "said": said})
    return display_results


def run_sequence_match(target_words: list, said_words: list) -> list:
    """
    Run SequenceMatcher on two normalized word lists.
    Returns word_results: list of { word, matched, said } for each target word.
    """
    word_results = [{"word": tw, "matched": False, "said": ""} for tw in target_words]
    matcher = difflib.SequenceMatcher(None, target_words, said_words, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                word_results[i1 + k]["matched"] = True
                word_results[i1 + k]["said"] = said_words[j1 + k]
        elif tag == "replace":
            for k in range(i1, i2):
                sw_idx = j1 + (k - i1)
                if sw_idx < j2:
                    word_results[k]["said"] = said_words[sw_idx]
    return word_results


def analyze_mismatches(target: str, transcription: str, mismatches: list, client,
                       lang: str = "fr") -> list:
    """
    Call Mistral to get pronunciation tips for each mismatch.
    client: a Mistral client instance (passed in to avoid duplicating client setup).
    Returns list of feedback dicts.
    """
    if not mismatches:
        return []

    payload = (
        f"Target phrase: {target}\n"
        f"Student said: {transcription}\n"
        f"Mismatches: {json.dumps(mismatches)}"
    )
    fallback = [{"target_word": m["target_word"], "said": m["said"], "tip": "", "is_grammar": False, "grammar_note": ""} for m in mismatches]
    try:
        raw = client.chat.complete(
            model="mistral-small-latest",
            messages=[
                {"role": "system", "content": _lang.get(lang).ANALYSIS_SYSTEM},
                {"role": "user", "content": payload},
            ],
            temperature=0.0,
            max_tokens=700,
        ).choices[0].message.content.strip()
        if not raw:
            return fallback
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json\n"):
                raw = raw[5:]
            raw = raw.rstrip()
        feedback = json.loads(raw).get("feedback", [])
        # Index Mistral's tips by normalised target word. Mistral normalises ‿/⁀
        # to spaces when echoing target_word back, so key on the stripped form.
        by_word = {}
        for item in feedback:
            key = re.sub(r'[‿⁀]', ' ', item.get('target_word', '')).strip().lower()
            if key and key not in by_word:
                by_word[key] = item
        # Emit exactly one entry per mismatch, in order — Mistral sometimes drops
        # mismatches it deems unrelated, which silently hides wrong words. The
        # mismatch list is the source of truth for count/order; enrich with the
        # matching tip when present, otherwise fall back to a tip-less entry.
        result = []
        for m in mismatches:
            key = re.sub(r'[‿⁀]', ' ', m['target_word']).strip().lower()
            item = by_word.get(key)
            if item:
                result.append({
                    "target_word": m["target_word"],
                    "said": m.get("said", ""),
                    "tip": item.get("tip", ""),
                    "is_grammar": item.get("is_grammar", False),
                    "grammar_note": item.get("grammar_note", ""),
                })
            else:
                result.append({
                    "target_word": m["target_word"],
                    "said": m.get("said", ""),
                    "tip": "",
                    "is_grammar": False,
                    "grammar_note": "",
                })
        return result
    except Exception:
        return fallback
