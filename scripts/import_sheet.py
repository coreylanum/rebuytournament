"""Import the 2026 Google Sheet export (File > Download > .xlsx) into a store.

Local:   python scripts/import_sheet.py 2026.xlsx [--store data/store]
AWS:     python scripts/import_sheet.py 2026.xlsx --bucket <DataBucketName> \
             --site-bucket <site bucket> --distribution <id> --publish

Players already in the registry keep their PokerNow IDs; sheet data is merged
in. Sheet records only overwrite other sheet records, never log-based ones.
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pokerleague.importers.sheet2026 import import_workbook  # noqa: E402
from pokerleague.league import get_tournament, load_registry, save_registry, save_tournament  # noqa: E402
from pokerleague.store import LocalStore, S3Store  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xlsx")
    ap.add_argument("--store", default="data/store", help="local store folder (ignored with --bucket)")
    ap.add_argument("--bucket", help="S3 data bucket (from the stack's DataBucketName output)")
    ap.add_argument("--publish", action="store_true", help="rebuild and publish the website afterwards")
    ap.add_argument("--site-bucket", help="S3 website bucket, for --publish")
    ap.add_argument("--distribution", help="CloudFront distribution id, for --publish")
    args = ap.parse_args()
    store = S3Store(args.bucket) if args.bucket else LocalStore(args.store)

    sheet_registry, records, warnings = import_workbook(args.xlsx)
    registry = load_registry(store)
    for p in sheet_registry.players.values():
        registry.add(p)
    save_registry(store, registry)

    written = 0
    for record in records:
        existing = get_tournament(store, record["date"])
        if existing and existing.get("source") != "sheet":
            print(f"skip {record['date']}: a log-based record already exists")
            continue
        save_tournament(store, record)
        written += 1
    print(f"{len(registry.players)} players, {written} tournaments imported")
    for w in warnings:
        print("WARNING:", w)

    if args.publish:
        if args.site_bucket:
            os.environ["SITE_BUCKET"] = args.site_bucket
        if args.distribution:
            os.environ["DISTRIBUTION_ID"] = args.distribution
        from pokerleague.outbound import publish_site

        print(publish_site(store))


if __name__ == "__main__":
    main()
