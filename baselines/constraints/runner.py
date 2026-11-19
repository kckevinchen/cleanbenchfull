"""Run native cleaning with the fixed full-paper constraint pool and score cutoff."""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import os
import signal
import subprocess
import sys
from pathlib import Path

from baselines.common import NATIVE_ROOT, atomic_json, fingerprint, read_json, read_table, sha
from baselines.constraints.selection import CONFIG, load, select


def run_table(method, dataset, inputs, output, specs, settings, runtime, hashes):
    dirty = inputs / dataset / "dirty.csv"
    headers, rows = read_table(dirty)
    if fingerprint(rows) != hashes[dataset]:
        raise ValueError(f"Dirty input differs from constraint generation input: {dataset}")
    work = output / "tables" / dataset
    work.mkdir(parents=True)
    if not specs:
        atomic_json(work / "result.json", dict(status="complete", changes=[], native_rules=0))
        return dataset, "complete", []
    alias = work / "positional.csv"
    with alias.open("w", newline="") as stream:
        csv.writer(stream, lineterminator="\n").writerows(
            [[f"C{i}" for i in range(len(headers))], *rows]
        )
    recipe = dict(
        method=method,
        dirty_csv=str(alias),
        dirty_sha256=sha(alias),
        specs=specs,
        seed=runtime["seed"],
        settings=settings,
        work=str(work / "native"),
        output=str(work / "result.json"),
        source_sha256={},
        project=str(NATIVE_ROOT),
    )
    recipe["recipe_sha256"] = fingerprint(recipe)
    path = work / "recipe.json"
    atomic_json(path, recipe)
    with (work / "worker.log").open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "baselines.constraints.rich_native_runtime",
                "--recipe",
                str(path),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={
                **os.environ,
                "PYTHONHASHSEED": "0",
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "SPARK_LOCAL_IP": "127.0.0.1",
            },
        )
        try:
            code = process.wait(timeout=runtime["outer_timeout_seconds"])
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            atomic_json(
                work / "result.json", dict(status="failed", error="native timeout", changes=[])
            )
            code = -1
    result = read_json(work / "result.json", {})
    complete = code == 0 and result.get("status") == "complete"
    if not complete:
        if not result:
            atomic_json(
                work / "result.json", dict(status="failed", error=f"worker exit {code}", changes=[])
            )
        return dataset, "failed", []
    return dataset, "complete", [dict(dataset=dataset, **p) for p in result["changes"]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--method", required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument("--threshold", type=int, default=20)
    choice.add_argument("--bank", help="Saved numeric or shuffled bank identifier")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--limit", type=int, default=0, help="Smoke run; other tables remain unknown"
    )
    args = parser.parse_args()
    pool, selections = load()
    if args.method not in pool["methods"] or args.threshold not in pool["thresholds"]:
        parser.error("Unknown method or threshold")
    if args.workers < 1 or args.limit < 0:
        parser.error("Workers must be positive and limit nonnegative")
    inputs, output = args.inputs.resolve(), args.output.resolve()
    entries = read_json(inputs / "_manifest.json")["datasets"]
    hashes, runtime = read_json(CONFIG / "input_hashes.json"), read_json(CONFIG / "runtime.json")
    if set(entries) != set(hashes):
        raise ValueError("Expected all 1,000 benchmark inputs")
    chosen = sorted(entries)[: args.limit or None]
    specs = {
        ds: select(pool, selections, args.method, ds, args.threshold, args.bank) for ds in chosen
    }
    # Verify the full selected input population before starting any native process.
    for ds in chosen:
        if fingerprint(read_table(inputs / ds / "dirty.csv")[1]) != hashes[ds]:
            raise ValueError(f"Dirty table changed: {ds}")
    output.mkdir(parents=True, exist_ok=False)
    states, predictions = dict.fromkeys(entries, "unknown"), []
    atomic_json(
        output / "run.json",
        dict(
            method=args.method,
            threshold=args.threshold,
            bank=args.bank,
            seed=runtime["seed"],
            workers=args.workers,
            tables=len(chosen),
            partial=len(chosen) != len(entries),
            pool_sha256=sha(CONFIG / "pool.json"),
        ),
    )
    with cf.ThreadPoolExecutor(max_workers=args.workers) as executor:
        jobs = [
            executor.submit(
                run_table,
                args.method,
                ds,
                inputs,
                output,
                specs[ds],
                runtime["methods"][args.method],
                runtime,
                hashes,
            )
            for ds in chosen
        ]
        for index, job in enumerate(cf.as_completed(jobs), 1):
            ds, status, changes = job.result()
            states[ds] = status
            predictions.extend(changes)
            if index % 25 == 0 or index == len(jobs):
                atomic_json(output / "progress.json", dict(done=index, total=len(jobs)))
                print(f"{index}/{len(jobs)}", flush=True)
    import json

    with (output / "predictions.jsonl").open("w") as stream:
        for row in sorted(predictions, key=lambda r: (r["dataset"], r["row"], r["col"])):
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    atomic_json(output / "execution.json", states)


if __name__ == "__main__":
    main()
