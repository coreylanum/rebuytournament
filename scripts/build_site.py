"""Build the website from a local store into a folder for previewing.

Usage:
  python scripts/build_site.py [--store data/store] [--out build/site]
Then serve it, e.g. `python -m http.server -d build/site 8765`.
"""

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pokerleague.site import build_site_files, write_local  # noqa: E402
from pokerleague.store import LocalStore  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", default="data/store")
    ap.add_argument("--out", default="build/site")
    args = ap.parse_args()
    files = build_site_files(LocalStore(args.store))
    shutil.rmtree(args.out, ignore_errors=True)
    write_local(files, args.out)
    print(f"wrote {len(files)} files to {args.out}")


if __name__ == "__main__":
    main()
