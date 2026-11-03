"""Put the demo back in its initial state, to rehearse the video.

- Empties the portal database (also works while the portal is running).
- Regenerates the 30-invoice Excel file.
- Clears Edith's memory (buffer/: frames, audio, timeline).
- Empties the workshop (tools/*, keeping tools/.gitkeep) and the learned plans (state/plans),
  unless --keep-tools is given.

Run: .venv\\Scripts\\python demo\\demo_reset.py [--keep-tools]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "portal"))

import make_invoices  # noqa: E402
from app import DATA_DIR, DEFAULT_DB, reset_db  # noqa: E402


def clear_dir(folder: Path, keep: set[str] = frozenset()) -> int:
    removed = 0
    if not folder.exists():
        return 0
    for item in folder.iterdir():
        if item.name in keep:
            continue
        try:
            shutil.rmtree(item) if item.is_dir() else item.unlink()
            removed += 1
        except PermissionError:
            print(f"  could not remove {item} (in use?)")
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--keep-tools", action="store_true", help="keep the workshop (tools/) and plans")
    args = parser.parse_args()

    reset_db(DEFAULT_DB)
    extra = [p for p in DATA_DIR.glob("*.db") if p != DEFAULT_DB]
    for p in extra:  # temporary verification databases
        p.unlink(missing_ok=True)
    print(f"portal db emptied: {DEFAULT_DB} (+{len(extra)} temp dbs removed)")
    print(f"excel regenerated: {make_invoices.write()}")
    buffer = ROOT / "buffer"
    n = clear_dir(buffer / "frames") + clear_dir(buffer / "audio")
    for name in ("timeline.jsonl", "timeline.jsonl.tmp"):
        (buffer / name).unlink(missing_ok=True)
    print(f"buffer cleared: {n} files")
    if not args.keep_tools:
        print(f"workshop cleared: {clear_dir(ROOT / 'tools', keep={'.gitkeep'})} machines, "
              f"{clear_dir(ROOT / 'state' / 'plans')} plans")


if __name__ == "__main__":
    main()
