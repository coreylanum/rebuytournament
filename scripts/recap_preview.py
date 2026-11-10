"""Generate the recap for one tournament with Claude and print it.

Usage:
  python scripts/recap_preview.py 2026-11-03 [--store data/store] [--save]

--save stores the recap on the tournament record (like "Write a new one" in the admin page).
The Anthropic API key comes from ANTHROPIC_API_KEY, or else from the file
data/private/anthropic_api_key (gitignored; put only the key in it).
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("ANTHROPIC_API_KEY_FILE", str(ROOT / "data" / "private" / "anthropic_api_key"))

from pokerleague.league import all_tournaments, get_tournament, load_registry, now, save_tournament  # noqa: E402
from pokerleague.recap import build_facts, generate_recap  # noqa: E402
from pokerleague.store import LocalStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("date")
    ap.add_argument("--store", default="data/store")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()
    store = LocalStore(args.store)
    record = get_tournament(store, args.date)
    if not record or not record.get("analysis"):
        sys.exit(f"no log-based tournament for {args.date}")
    facts = build_facts(record, all_tournaments(store), load_registry(store).public())
    result = generate_recap(facts)
    print(f"[source: {result['source']}{' / ' + result['model'] if result.get('model') else ''}"
          f"{' / ' + result['reason'] if result.get('reason') else ''}]\n")
    print(result["text"])
    if args.save:
        record["recap"] = {**result, "generated_at": now(), "edited": False}
        save_tournament(store, record)
        print("\n(saved to the tournament record)")


if __name__ == "__main__":
    main()
