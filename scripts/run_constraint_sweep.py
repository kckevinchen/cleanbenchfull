"""Run the full-paper constraint threshold experiment in Docker."""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.run_table3 import prepare_inputs  # noqa: E402
from src.reproducibility.constraints import METHODS, cross_fit  # noqa: E402
from src.reproducibility.table3 import metrics, read, records, score_run, write_csv  # noqa: E402


def image(method):
    return (
        "cleanbenchfull:constraint-uniclean"
        if method == "uniclean"
        else "cleanbenchfull:constraint-classical"
    )


def build_command(method):
    target = "constraint-uniclean" if method == "uniclean" else "constraint-classical"
    return [
        "docker",
        "build",
        "--target",
        target,
        "-t",
        image(method),
        "-f",
        str(ROOT / "docker/Dockerfile"),
        str(ROOT),
    ]


def run_command(method, inputs, output, bank, workers):
    return [
        "docker",
        "run",
        "--rm",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--env",
        "HOME=/tmp",
        "--mount",
        f"type=bind,source={inputs},target=/inputs,readonly",
        "--mount",
        f"type=bind,source={output.parent},target=/results",
        image(method),
        "--method",
        method,
        "--inputs",
        "/inputs",
        "--output",
        f"/results/{output.name}",
        "--bank",
        bank,
        "--workers",
        str(workers),
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["build", "prepare", "run"])
    parser.add_argument("--method", action="append", choices=list(METHODS))
    parser.add_argument("--threshold", type=int, action="append", choices=[0, 20, 40, 60, 80, 90])
    parser.add_argument(
        "--controls", action="store_true", help="Also execute all three shuffled-score sweeps"
    )
    parser.add_argument("--output", type=Path, default=ROOT / "outputs/constraint_sweep")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    methods = args.method or list(METHODS)
    if args.action == "build":
        seen = set()
        for method in methods:
            command = build_command(method)
            if image(method) in seen:
                continue
            seen.add(image(method))
            print(shlex.join(command), flush=True)
            if not args.dry_run:
                subprocess.run(command, check=True)
        return
    output = args.output.resolve()
    reference = read(ROOT / "reproducibility/table3/reference.json")
    inputs = output / "inputs"
    plans = read(ROOT / "reproducibility/constraints/plans.json")
    thresholds = args.threshold or [0, 20, 40, 60, 80, 90]
    if args.workers < 1:
        parser.error("Workers must be positive")
    if not args.dry_run:
        if output.exists():
            parser.error("Choose a new output directory")
        output.mkdir(parents=True)
        prepare_inputs(reference, ROOT / "benchmark/sanitized_sources.zip", inputs)
    if args.action == "prepare":
        return
    policies = list(plans) if args.controls else ["numeric"]
    observations, cache, selected = [], {}, []
    folds = read(ROOT / "reproducibility/constraints/folds.json")
    for method in methods:
        for policy in policies:
            curves = {}
            for threshold in thresholds:
                bank = plans[policy][str(threshold)]
                destination = output / "runs" / method / bank
                key = method, bank
                if key not in cache:
                    command = run_command(method, inputs, destination, bank, args.workers)
                    print(shlex.join(command), flush=True)
                    cache[key] = None
                    if not args.dry_run:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        subprocess.run(command, check=True)
                        cache[key] = score_run(
                            records(destination / "predictions.jsonl"),
                            reference,
                            read(destination / "execution.json"),
                        )
                if not args.dry_run:
                    curves[threshold] = cache[key]
                    values, totals = metrics(cache[key])
                    observations.append(
                        dict(
                            method=METHODS[method],
                            policy=policy,
                            threshold=threshold,
                            **values,
                            failed_tables=sum(
                                t["execution"] != "complete" for t in cache[key].values()
                            ),
                        )
                    )
            if not args.dry_run and set(thresholds) == {0, 20, 40, 60, 80, 90}:
                values, choices = cross_fit(curves, folds)
                selected.append(
                    dict(method=METHODS[method], policy=policy, **values, thresholds=str(choices))
                )
    if not args.dry_run:
        write_csv(output / "thresholds.csv", observations)
        if selected:
            write_csv(output / "selection.csv", selected)


if __name__ == "__main__":
    main()
