"""Point git at the versioned .githooks/ folder so the safety pre-commit hook always runs.

Run: .venv\\Scripts\\python scripts\\install_hooks.py
"""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=ROOT, check=True)
    hooks_path = subprocess.run(["git", "config", "core.hooksPath"], cwd=ROOT,
                                capture_output=True, text=True, check=True).stdout.strip()
    print(f"core.hooksPath = {hooks_path}  ({ROOT / '.githooks' / 'pre-commit'})")


if __name__ == "__main__":
    main()
