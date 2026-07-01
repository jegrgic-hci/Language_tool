"""
Offline content-bank seeder (Phase 3 — hybrid seeding).

Pre-generates cohesive paragraphs across core level x topic (x style) buckets and
banks them, which also seeds the reusable phrase pool that shadow/dictation draw
from. This gives day-one novelty for common buckets; the long tail and user-added
subjects fill organically at runtime.

Reuses the server's own ``_generate_and_bank_passage`` so seeded content is
identical to what the live app produces. Bounded by the monthly Chirp budget
guard in ``library_store`` — when the ~900k cap is hit, seeding stops (further
synthesis would fall back to edge-tts anyway).

Requires the same env as the app: MISTRAL_API_KEY (generation), GOOGLE_TTS_API_KEY
(Chirp), and optionally the R2_* vars (so seeded audio + records land in the
shared library used by production).

Usage:
    python seed_bank.py --per-bucket 3
    python seed_bank.py --levels A1,A2,B1 --styles story,dialogue --per-bucket 2
    python seed_bank.py --topics "au restaurant,les vacances" --per-bucket 5
    python seed_bank.py --dry-run          # list buckets, generate nothing
"""

import argparse
import asyncio
from datetime import datetime

import library_store
import server  # reuses _generate_and_bank_passage, TOPICS

DEFAULT_LEVELS = ["A1", "A2", "B1", "B2", "C1"]
DEFAULT_STYLES = ["story"]


def _parse_list(val, default):
    if not val:
        return default
    return [x.strip() for x in val.split(",") if x.strip()]


_FREE_TIER = 1_000_000  # Chirp3-HD free characters per month


def _days_until_reset(now: datetime) -> int:
    """Days until the monthly counter resets (1st of next month, UTC)."""
    nxt = datetime(now.year + (now.month == 12), (now.month % 12) + 1, 1)
    return (nxt - now).days


def print_status():
    """Show remaining free Chirp allotment + days to reset — run this from your
    end-of-cycle calendar reminder to decide how much to top up."""
    used = library_store.chars_used_this_month()
    soft_cap = int(library_store._MONTHLY_CHAR_CAP)
    days_left = _days_until_reset(datetime.utcnow())
    print("Chirp free-tier status (this calendar month, UTC):")
    print("  used:            {:>9,} chars".format(used))
    print("  free tier:       {:>9,} chars  (unused resets to 0 each month)".format(_FREE_TIER))
    print("  remaining (free):{:>9,} chars".format(max(_FREE_TIER - used, 0)))
    print("  soft cap:        {:>9,} chars  (seeding stops here to leave headroom)".format(soft_cap))
    print("  room to seed:    {:>9,} chars".format(max(soft_cap - used, 0)))
    print("  ~{} day(s) until reset".format(days_left))
    print("  chirp enabled: {}  |  storage: {}".format(
        library_store.chirp_enabled(), library_store.storage_backend()))
    if days_left <= 6 and used < soft_cap * 0.5:
        print("\n  >> Lots of free allotment unused and reset is near — consider:")
        print("     python seed_bank.py --per-bucket 3")


async def _seed(levels, topics, styles, per_bucket, dry_run):
    planned = len(levels) * len(topics) * len(styles) * per_bucket
    print("Seeding up to {} passages ({} levels x {} topics x {} styles x {} each)".format(
        planned, len(levels), len(topics), len(styles), per_bucket))
    if dry_run:
        for level in levels:
            for topic in topics:
                for style in styles:
                    print("  would seed: {} / {} / {}  x{}".format(level, topic, style, per_bucket))
        return

    if not library_store.chirp_enabled():
        print("WARNING: GOOGLE_TTS_API_KEY not set — audio would fall back to edge-tts "
              "(seeded audio would not be Chirp). Aborting.")
        return

    banked = 0
    for level in levels:
        for topic in topics:
            for style in styles:
                for _ in range(per_bucket):
                    if not library_store._budget_available():
                        print("Monthly Chirp budget cap reached ({} chars). Stopping. "
                              "Banked {} passages this run.".format(
                                  library_store.chars_used_this_month(), banked))
                        return
                    try:
                        p = await server._generate_and_bank_passage(level, topic, style)
                        banked += 1
                        print("  banked {} / {} / {}  ({} phrases)".format(
                            level, topic, style, len(p.get("phrase_ids", []))))
                    except Exception as e:
                        print("  skip {} / {} / {} — {}".format(level, topic, style, e))
    print("Done. Banked {} passages. Chirp chars used this month: {}".format(
        banked, library_store.chars_used_this_month()))


def main():
    ap = argparse.ArgumentParser(description="Seed the content bank.")
    ap.add_argument("--levels", help="comma-separated CEFR levels", default="")
    ap.add_argument("--topics", help="comma-separated topics (default: paragraph_engine.TOPICS)", default="")
    ap.add_argument("--styles", help="comma-separated styles", default="")
    ap.add_argument("--per-bucket", type=int, default=3, help="passages per bucket")
    ap.add_argument("--dry-run", action="store_true", help="list buckets, generate nothing")
    ap.add_argument("--status", action="store_true", help="show remaining free allotment + days to reset, then exit")
    args = ap.parse_args()

    if args.status:
        print_status()
        return

    levels = _parse_list(args.levels, DEFAULT_LEVELS)
    topics = _parse_list(args.topics, list(server.TOPICS))
    styles = _parse_list(args.styles, DEFAULT_STYLES)

    asyncio.run(_seed(levels, topics, styles, args.per_bucket, args.dry_run))


if __name__ == "__main__":
    main()
