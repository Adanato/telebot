"""One-shot benchmark: sample → autolabel → eval, for fast parser iteration.

Use this when you've just changed the parser prompt and want a quick signal.
Runs against the 1d fixture with high concurrency.

Usage:
    uv run python benchmark/quick.py
    uv run python benchmark/quick.py --model claude-sonnet-4-6
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

BENCH_DIR = Path(__file__).parent


def run(cmd: list[str]) -> None:
    print(f"\n$ {' '.join(cmd)}")
    r = subprocess.run(cmd, cwd=BENCH_DIR.parent)
    if r.returncode != 0:
        sys.exit(r.returncode)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default="1d")
    ap.add_argument("--model", default="claude-haiku-4-5")
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--relabel", action="store_true",
                    help="Re-run autolabel (normally cached from prior run)")
    args = ap.parse_args()

    labels_path = BENCH_DIR / "labels" / f"{args.fixture}.yaml"
    if args.relabel or not labels_path.exists():
        run(["uv", "run", "python", "benchmark/autolabel.py", "--fixture", args.fixture])

    run([
        "uv", "run", "python", "benchmark/eval.py",
        "--fixture", args.fixture,
        "--model", args.model,
        "--effort", args.effort,
    ])


if __name__ == "__main__":
    main()
