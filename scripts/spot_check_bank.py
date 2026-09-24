"""Prints a random sample of freshly-banked content so a human can eyeball quality
(catch gibberish, repetition, or off-topic generations) without opening R2 directly.

Usage:
    python scripts/spot_check_bank.py --n 6
"""
import argparse
import random
import sys

sys.path.insert(0, __file__.rsplit("/scripts/", 1)[0])

from dotenv import load_dotenv
load_dotenv()

import content_bank  # noqa: E402
import library_store  # noqa: E402


def _sample_ids(kind, register, level, topic, style, n):
    ids = content_bank.bucket_ids(kind, register, level, topic, style)
    random.shuffle(ids)
    return ids[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=2, help="samples per bucket kind")
    args = ap.parse_args()

    stats = content_bank.bank_stats()
    buckets = stats["buckets"]
    random.shuffle(buckets)

    print(f"Bank: {stats['total_units']} units / {stats['bucket_count']} buckets  "
          f"| Chirp {stats['chirp_chars_used_month']:,}/{stats['chirp_char_cap']:,} chars\n")

    shown = 0
    for b in buckets:
        if shown >= args.n * 3:
            break
        ids = _sample_ids(b["kind"], b["register"], b["level"], b["topic"], b["style"], 1)
        if not ids:
            continue
        rec = content_bank._load(b["kind"], ids[0])
        if not rec:
            continue
        shown += 1
        print("=" * 70)
        print(f"[{b['category']}] level={b['level']} topic={b['topic']} style={b['style']} "
              f"(bucket depth {b['count']})")
        if b["kind"] == "phrase":
            print(f"  \"{rec['text']}\"")
        elif "lines" in rec:  # dialogue
            print(f"  Title: {rec.get('title', '')}")
            for ln in rec.get("lines", [])[:6]:
                print(f"  {ln['speaker']}: {ln['text']}")
            if len(rec.get("lines", [])) > 6:
                print(f"  ... ({len(rec['lines']) - 6} more lines)")
        elif "text" in rec:  # listen & answer
            print(f"  {rec['text'][:400]}{'...' if len(rec['text']) > 400 else ''}")
        else:  # paragraph passage (phrase_ids)
            phrases = content_bank.passage_phrases(rec)
            print("  " + " ".join(p["text"] for p in phrases))
        print()


if __name__ == "__main__":
    main()
