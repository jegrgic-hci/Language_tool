"""One-off content-bank filler: pre-generates + banks Chirp3-HD content to use up
this month's free Chirp allocation before it resets, without going through live
student traffic.

Covers three content types:
  - standard pool (paragraph + its per-sentence phrases) — feeds shadow/paragraph/dictation
  - dialogue (Dialogue French) — every line is synthesized immediately for playback
  - listen & answer (passage + questions) — text is generated same as the live route,
    but audio is warmed eagerly here (--listen-buckets / --warm-existing-listen)
    instead of waiting on the frontend's lazy /tts call, since virtually every banked
    passage gets listened to eventually anyway — this just does that spend now while
    there's slack, and removes first-listener latency.

Run in small batches on purpose (--standard-buckets / --dialogue-buckets /
--listen-buckets) so usage impact can be checked on the Mistral console between runs.
Safe to re-run: buckets already at target depth are skipped without any new API calls.

Usage:
    source .venv/bin/activate
    python scripts/fill_bank.py --standard-buckets 2 --standard-depth 5 \
        --dialogue-buckets 1 --dialogue-depth 3 --listen-buckets 2 --listen-depth 3
"""
import argparse
import asyncio
import itertools
import json
import sys
import time

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from dotenv import load_dotenv
load_dotenv()

import server  # noqa: E402  (import-safe: uvicorn only runs under __main__)
import content_bank  # noqa: E402
import library_store  # noqa: E402

# Real canonical buckets, taken from the actual UI chips in static/index.html —
# these are the combos students can actually land on, not the stale module-level
# TOPICS/LEVELS lists.
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]

STANDARD_TOPICS = [
    "la nature", "au restaurant", "les salutations", "les loisirs",
    "la météo", "la famille", "les transports", "le marché",
]
PARAGRAPH_STYLES = ["story", "educational", "howto", "vocabulary", "proverbs", "opinion"]

DIALOGUE_TOPICS = [
    "deux amis qui se racontent leur week-end",
    "organiser une soirée entre amis",
    "se plaindre du travail et des collègues",
    "discuter d'un film ou d'une série qu'ils ont vu",
    "commander et discuter dans un café à Marseille",
    "raconter un imprévu ou une galère du quotidien",
    "parler de la bouffe et de cuisine",
    "potins et nouvelles d'amis communs",
]
DIALOGUE_TYPES = ["conversational", "everyday", "professional"]

LISTEN_TOPICS = [
    "la vie quotidienne", "la nature", "la culture française", "l'actualité",
    "la gastronomie", "les voyages", "la santé", "le travail", "la technologie",
    "Marseille",
]


def _retry(fn, *args, tries: int = 3, delay: float = 5.0, default=None, **kwargs):
    """Retry a store read/write a few times before giving up, so a brief network
    drop doesn't crash the batch. Returns `default` if all attempts fail."""
    for attempt in range(tries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            print(f"  [warn] {fn.__name__} failed ({e}); retry {attempt + 1}/{tries}...")
            time.sleep(delay)
    return default


def _budget_headroom() -> int:
    """Headroom in chars, or None if the usage read itself failed (e.g. a transient
    network drop) — treated as "keep going, retry next loop" rather than a hard stop,
    so a brief connectivity blip doesn't crash the whole batch."""
    for attempt in range(3):
        try:
            return library_store.monthly_char_cap() - library_store.chars_used_this_month()
        except Exception as e:
            print(f"  [warn] budget check failed ({e}); retrying...")
            time.sleep(5)
    return None  # couldn't reach the store; caller treats this as "not exhausted yet"


def _print_budget(label: str) -> None:
    try:
        used = library_store.chars_used_this_month()
        cap = library_store.monthly_char_cap()
        print(f"[{label}] Chirp chars used this month: {used:,} / {cap:,} (headroom {cap - used:,})")
    except Exception as e:
        print(f"[{label}] could not read Chirp usage: {e}")


async def _fill_standard_bucket(level: str, topic: str, depth: int, sleep_s: float) -> int:
    """Bring the (standard, level, topic) phrase pool up to `depth` paragraph-gen
    calls, rotating through paragraph styles. Returns calls actually made."""
    style_cycle = itertools.cycle(PARAGRAPH_STYLES)
    current = _retry(content_bank.count, "phrase", "standard", level, topic, "", default=0)
    made = 0
    while current < depth:
        headroom = _budget_headroom()
        if headroom is not None and headroom <= 0:
            print("  Chirp monthly cap reached — stopping.")
            break
        style = next(style_cycle)
        try:
            await server._generate_and_bank_passage(level, topic, style)
        except Exception as e:
            print(f"  [skip] {level}/{topic}/{style}: {e}")
            await asyncio.sleep(sleep_s)
            continue
        made += 1
        current = _retry(content_bank.count, "phrase", "standard", level, topic, "", default=current + 1)
        print(f"  + {level} / {topic} / {style}  (phrase pool now {current})")
        await asyncio.sleep(sleep_s)
    return made


async def _generate_dialogue(level: str, topic: str, dtype: str) -> dict:
    """Same generation path as POST /natural/generate, minus the Request/analytics
    wrapper — always generates fresh (does not check the bank first), since this
    script's whole job is to grow the bank."""
    voice_a, voice_b = server.pick_dialogue_voices()
    name_a = server.voice_display_name(voice_a)
    name_b = server.voice_display_name(voice_b)
    q_count = server._NATURAL_Q_COUNT.get(level, 4)

    user_prompt = (
        f"CEFR level: {level}\n"
        f"Topic / situation: {topic}\n"
        f"Number of questions: {q_count}\n"
        f"Speaker names: the two friends are named {name_a} and {name_b}. Use these "
        f"exact names as the \"speaker\" label on every line, and refer to them by "
        f"name in all questions, options and explanations.\n\n"
        "Write a natural spoken French dialogue between these two friends, plus vocab_preview and questions now."
    )
    resp = await asyncio.to_thread(lambda: server._mistral.chat.complete(
        model=server._MODEL,
        messages=[
            {"role": "system", "content": server._natural_system(dtype)},
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

    for q in questions:
        opts = q.get("options")
        if not isinstance(opts, list) or len(opts) < 2:
            continue
        ci = q.get("correct_index", 0)
        if not isinstance(ci, int) or not (0 <= ci < len(opts)):
            ci = 0
        correct_opt = opts[ci]
        import random
        random.shuffle(opts)
        q["options"] = opts
        q["correct_index"] = opts.index(correct_opt)

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
            role = "A" if prev_role == "B" else "B"
        prev_role = role
        banked_lines.append({
            "speaker": name_a if role == "A" else name_b, "role": role,
            "text": text, "voice": voice_a if role == "A" else voice_b,
        })

    await server._render_dialogue_lines(banked_lines)  # synthesizes + caches audio
    return content_bank.add_passage(
        "casual", level, topic, voice_a, [], style=dtype,
        questions=questions, vocab_preview=vocab_preview,
        payload={"title": title, "lines": banked_lines, "voices": [voice_a, voice_b]},
    )


async def _fill_dialogue_bucket(level: str, topic: str, dtype: str, depth: int, sleep_s: float) -> int:
    current = _retry(content_bank.count, "passage", "casual", level, topic, dtype, default=0)
    made = 0
    while current < depth:
        headroom = _budget_headroom()
        if headroom is not None and headroom <= 0:
            print("  Chirp monthly cap reached — stopping.")
            break
        try:
            await _generate_dialogue(level, topic, dtype)
        except Exception as e:
            print(f"  [skip] {level}/{topic}/{dtype}: {e}")
            await asyncio.sleep(sleep_s)
            continue
        made += 1
        current = _retry(content_bank.count, "passage", "casual", level, topic, dtype, default=current + 1)
        print(f"  + {level} / {topic} / {dtype}  (dialogue pool now {current})")
        await asyncio.sleep(sleep_s)
    return made


async def _generate_listen_passage(level: str, topic: str) -> dict:
    """Same generation path as POST /listen/generate, minus the Request wrapper —
    always generates fresh. Warms the Chirp audio immediately (unlike the live
    route, which leaves it lazy for the frontend's /tts call)."""
    q_count = server._COMPREHENSION_Q_COUNT.get(level, 4)
    user_prompt = (
        f"CEFR level: {level}\n"
        f"Topic: {topic}\n"
        f"Number of paragraphs: 2\n"
        f"Number of questions: {q_count}\n\n"
        "Generate the passage, vocab_preview, and comprehension questions now."
    )
    resp = await asyncio.to_thread(lambda: server._mistral.chat.complete(
        model="mistral-small-latest",
        messages=[
            {"role": "system", "content": server._COMPREHENSION_SYSTEM},
            {"role": "user", "content": user_prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=2400,
    ))
    result = json.loads(resp.choices[0].message.content)
    passage = result.get("passage", "")
    questions = result.get("questions", [])
    vocab_preview = result.get("vocab_preview", [])

    import random
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

    voice = server.pick_narrator_voice()
    await server.generate_library_audio(passage, voice)  # warm the Chirp cache now
    return content_bank.add_passage(
        "listen", level, topic, voice, [], questions=questions,
        vocab_preview=vocab_preview, payload={"text": passage},
    )


async def _fill_listen_bucket(level: str, topic: str, depth: int, sleep_s: float) -> int:
    current = _retry(content_bank.count, "passage", "listen", level, topic, "", default=0)
    made = 0
    while current < depth:
        headroom = _budget_headroom()
        if headroom is not None and headroom <= 0:
            print("  Chirp monthly cap reached — stopping.")
            break
        try:
            await _generate_listen_passage(level, topic)
        except Exception as e:
            print(f"  [skip] {level}/{topic}: {e}")
            await asyncio.sleep(sleep_s)
            continue
        made += 1
        current = _retry(content_bank.count, "passage", "listen", level, topic, "", default=current + 1)
        print(f"  + {level} / {topic}  (listen pool now {current})")
        await asyncio.sleep(sleep_s)
    return made


async def _warm_existing_listen_audio(sleep_s: float) -> int:
    """One-time pass: synthesize Chirp audio now for every already-banked Listen &
    Answer passage that doesn't have it cached yet (content-addressed, so this is a
    no-op/cache-hit for any that already got played live)."""
    stats = _retry(content_bank.bank_stats, default=None)
    if not stats:
        return 0
    warmed = 0
    for b in stats["buckets"]:
        if b["category"] != "listen_answer":
            continue
        for uid in content_bank.bucket_ids(b["kind"], b["register"], b["level"], b["topic"], b["style"]):
            headroom = _budget_headroom()
            if headroom is not None and headroom <= 0:
                print("  Chirp monthly cap reached — stopping warm pass.")
                return warmed
            rec = content_bank.get_passage(uid)
            if not rec or not rec.get("text"):
                continue
            try:
                await server.generate_library_audio(rec["text"], rec.get("voice") or server.pick_narrator_voice())
            except Exception as e:
                print(f"  [skip warm] {uid}: {e}")
                continue
            warmed += 1
            print(f"  ~ warmed {b['level']}/{b['topic']} ({uid[:8]})")
            await asyncio.sleep(sleep_s)
    return warmed


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--standard-buckets", type=int, default=2,
                    help="how many (level, topic) standard combos to process this run")
    ap.add_argument("--standard-depth", type=int, default=5,
                    help="target phrase-pool depth per standard bucket")
    ap.add_argument("--dialogue-buckets", type=int, default=1,
                    help="how many (level, topic, type) dialogue combos to process this run")
    ap.add_argument("--dialogue-depth", type=int, default=3,
                    help="target dialogue-pool depth per dialogue bucket")
    ap.add_argument("--listen-buckets", type=int, default=0,
                    help="how many (level, topic) listen combos to process this run")
    ap.add_argument("--listen-depth", type=int, default=3,
                    help="target listen-pool depth per listen bucket")
    ap.add_argument("--warm-existing-listen", action="store_true",
                    help="also synthesize Chirp audio now for already-banked listen passages")
    ap.add_argument("--sleep", type=float, default=1.5,
                    help="seconds between Mistral calls (rate-limit safety)")
    args = ap.parse_args()

    if not library_store.chirp_enabled():
        print("Chirp not configured (GOOGLE_TTS_API_KEY missing) — aborting.")
        return

    _print_budget("before")

    all_standard = [(lvl, topic) for lvl in LEVELS for topic in STANDARD_TOPICS]
    all_dialogue = [(lvl, topic, dtype) for lvl in LEVELS for topic in DIALOGUE_TOPICS for dtype in DIALOGUE_TYPES]

    print(f"\nStandard buckets this run: {args.standard_buckets} of {len(all_standard)} total")
    for level, topic in all_standard[:args.standard_buckets]:
        print(f"[standard] {level} / {topic} -> target depth {args.standard_depth}")
        await _fill_standard_bucket(level, topic, args.standard_depth, args.sleep)

    print(f"\nDialogue buckets this run: {args.dialogue_buckets} of {len(all_dialogue)} total")
    for level, topic, dtype in all_dialogue[:args.dialogue_buckets]:
        print(f"[dialogue] {level} / {topic} / {dtype} -> target depth {args.dialogue_depth}")
        await _fill_dialogue_bucket(level, topic, dtype, args.dialogue_depth, args.sleep)

    all_listen = [(lvl, topic) for lvl in LEVELS for topic in LISTEN_TOPICS]
    print(f"\nListen buckets this run: {args.listen_buckets} of {len(all_listen)} total")
    for level, topic in all_listen[:args.listen_buckets]:
        print(f"[listen] {level} / {topic} -> target depth {args.listen_depth}")
        await _fill_listen_bucket(level, topic, args.listen_depth, args.sleep)

    if args.warm_existing_listen:
        print("\nWarming Chirp audio for already-banked listen passages...")
        n = await _warm_existing_listen_audio(args.sleep)
        print(f"Warmed {n} passages.")

    print()
    _print_budget("after")
    stats = _retry(content_bank.bank_stats, default=None)
    if stats:
        print(f"Bank totals: {stats['totals']}  (bucket_count={stats['bucket_count']})")
        print(f"Ops this month: {stats['ops']}")


if __name__ == "__main__":
    asyncio.run(main())
