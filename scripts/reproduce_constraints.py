"""Reproduce constraint threshold curves and five-fold results without API keys."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.reproducibility.constraints import replay  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "build/constraints")
    args = parser.parse_args()
    result = replay(ROOT, args.output)
    (args.output / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print("MATCH" if result["match"] else "DIFFERENCES")
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["match"] else 1)


if __name__ == "__main__":
    main()
