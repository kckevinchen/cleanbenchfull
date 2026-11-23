"""Recompute the full-paper constraint experiment from saved predictions."""

from __future__ import annotations

import csv
import math
import statistics

from src.reproducibility.table3 import metrics, read, records, score_run, sha, write_csv

METHODS = {
    "bigdansing": "BigDansing",
    "holistic": "Holistic",
    "daisyp": "DaisyP",
    "horizon": "Horizon",
    "nadeef": "NADEEF",
    "llunatic": "LLUNATIC",
    "uniclean": "UniClean",
}


def peak_threshold(scores):
    """Summarize the observed full-benchmark sweep, breaking ties at the higher cutoff."""
    threshold = max(scores, key=lambda t: (scores[t]["f1"], t))
    return scores[threshold], threshold


def verify(root, repo):
    manifest = read(root / "manifest.json")
    if manifest["schema_version"] != 1:
        raise ValueError("Unsupported constraint artifact version")
    for relative, expected in manifest["sha256"].items():
        path = (root / relative).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != expected:
            raise ValueError(f"Constraint artifact checksum mismatch: {relative}")
    for path, key in [
        (repo / "reproducibility/table3/reference.json", "reference_sha256"),
        (repo / "baselines/constraints/config/pool.json", "pool_sha256"),
        (repo / "baselines/constraints/config/selections.json", "selection_sha256"),
    ]:
        if sha(path) != manifest[key]:
            raise ValueError(f"Constraint input checksum mismatch: {path.name}")
    return manifest


def compare_rows(rows, path, keys):
    with path.open(newline="") as stream:
        expected = list(csv.DictReader(stream))
    if len(rows) != len(expected):
        raise ValueError("Result row count differs")
    differences = []
    for row, gold in zip(rows, expected):
        if any(str(row[key]) != gold[key] for key in keys):
            raise ValueError("Result identities differ")
        for key, value in row.items():
            if key in keys or key not in gold:
                continue
            if not math.isclose(float(value), float(gold[key]), abs_tol=1e-9):
                differences.append(
                    dict(method=row["method"], metric=key, actual=value, expected=gold[key])
                )
    return differences


def replay(repo, output):
    root = repo / "reproducibility/constraints"
    manifest = verify(root, repo)
    reference = read(repo / "reproducibility/table3/reference.json")
    if reference["population"]["targets"] != 1178:
        raise ValueError("Expected the full-paper 1,178-requirement reference")
    plans = read(root / "plans.json")
    pool = read(repo / "baselines/constraints/config/pool.json")
    selections = read(repo / "baselines/constraints/config/selections.json")
    cache, curves_output, comparison = {}, [], []
    for method, name in METHODS.items():
        outcomes = {}
        for policy, plan in plans.items():
            curves = {}
            for threshold in manifest["thresholds"]:
                bank = plan[str(threshold)]
                key = method, bank
                if key not in cache:
                    run = manifest["runs"][bank][method]
                    cache[key] = score_run(
                        records(root / run["predictions"]), reference, read(root / run["execution"])
                    )
                tables = cache[key]
                values, counts = metrics(tables)
                if counts["tables"] != 1000 or counts["targets"] != 1178:
                    raise ValueError("Incomplete evaluation population")
                curves[threshold] = values
                if policy == "numeric":
                    ids = selections[bank]
                    consumed = sum(
                        method in r["native"]
                        for ds, rules in pool["tables"].items()
                        for r in rules
                        if r["rule"]["rule_id"] in ids.get(ds, [])
                    )
                    curves_output.append(
                        dict(
                            method=name,
                            threshold=threshold,
                            candidate_rules=sum(map(len, ids.values())),
                            consumed_rules=consumed,
                            **values,
                            tp=counts["tp"],
                            fp=counts["fp"],
                            fn=counts["fn"],
                            failed_tables=sum(
                                r["execution"] != "complete" for r in tables.values()
                            ),
                        )
                    )
            outcomes[policy] = peak_threshold(curves)
            if policy == "numeric":
                all_f1 = curves[0]["f1"]
        selected, threshold = outcomes["numeric"]
        random = [v["f1"] for p, (v, _) in outcomes.items() if p != "numeric"]
        comparison.append(
            dict(
                method=name,
                threshold=threshold,
                all_f1=all_f1,
                numeric_f1=selected["f1"],
                random_mean_f1=statistics.mean(random),
                random_min_f1=min(random),
                random_max_f1=max(random),
                precision=selected["precision"],
                recall=selected["recall"],
            )
        )
    differences = compare_rows(
        curves_output, root / "expected/thresholds.csv", ["method", "threshold"]
    )
    differences += compare_rows(comparison, root / "expected/comparison.csv", ["method", "threshold"])
    output.mkdir(parents=True, exist_ok=True)
    write_csv(output / "thresholds.csv", curves_output)
    write_csv(output / "comparison.csv", comparison)
    return dict(
        match=not differences,
        differences=differences,
        tables=1000,
        targets=1178,
        methods=len(METHODS),
        method_bank_runs=len(cache),
    )
