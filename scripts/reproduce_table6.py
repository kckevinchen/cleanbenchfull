"""Score the repair-only runs with the end-to-end correction metric."""

import argparse
import csv
import gzip
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.reproducibility.table3 import inside, metrics, read, score_run, sha  # noqa: E402

FIELDS = (
    "recall_typo-spelling",
    "recall_typo-formatting",
    "recall_inconsistency",
    "precision",
    "recall",
    "f1",
)


def reproduce(bundle, reference_path):
    manifest = read(bundle / "manifest.json")
    if sha(reference_path) != manifest["reference_sha256"]:
        raise ValueError("Table 3 scoring reference has changed")
    reference = read(reference_path)
    locations = {(r["dataset"], r["row"], r["col"]) for r in reference["targets"]}
    for group in reference["consistency_groups"]:
        locations.update((m["table"], m["local_row"], m["col"]) for m in group["members"])
    scope = {dataset for dataset, _, _ in locations}
    by_method = defaultdict(dict)
    categories = {}
    for name, digest in manifest["runs"].items():
        path = inside(bundle, name)
        if sha(path) != digest:
            raise ValueError(f"Repair-only run checksum mismatch for {name}")
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            run = json.load(stream)
        status = run["executions"]
        if set(status) != scope or any(
            v not in {"complete", "failed", "unknown"} for v in status.values()
        ):
            raise ValueError(f"Invalid table execution records in {name}")
        for row in run["predictions"]:
            cell = (row["dataset"], row["row"], row["col"])
            if cell not in locations or not isinstance(row["corrected_value"], str):
                raise ValueError(f"Invalid repair at {cell} in {name}")
        executions = {t: status.get(t, "unknown") for t in reference["tables"]}
        scores, _ = metrics(score_run(run["predictions"], reference, executions))
        method, rep = run["method"], run["repetition"]
        if (
            rep in by_method[method]
            or categories.setdefault(method, run["category"]) != run["category"]
        ):
            raise ValueError(f"Inconsistent run identity in {name}")
        by_method[method][rep] = scores

    rows = []
    for method in manifest["methods"]:
        runs = by_method[method]
        if set(runs) != set(range(1, manifest["methods"][method] + 1)):
            raise ValueError(f"Incomplete run set for {method}")
        rows.append(
            {
                "task": "correction",
                "category": categories[method],
                "method": method,
                "repetitions": len(runs),
                **{k: statistics.mean(r[k] for r in runs.values()) for k in FIELDS},
            }
        )
    if by_method.keys() != manifest["methods"].keys():
        raise ValueError("Repair-only method roster mismatch")
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "build/table6/repair_type_metrics.csv"
    )
    args = parser.parse_args()
    rows = reproduce(
        ROOT / "reproducibility/table6", ROOT / "reproducibility/table3/reference.json"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(
        f"Table 6: {len(rows)} methods, {sum(r['repetitions'] for r in rows)} runs. {args.output}"
    )


if __name__ == "__main__":
    main()
