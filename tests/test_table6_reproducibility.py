"""The repair-only diagnostic uses the complete location set and Table 3 scoring."""

import gzip
import json

import pytest

from scripts.reproduce_table6 import ROOT, reproduce
from src.reproducibility.table3 import metrics, read, score_run


def test_repair_only_uses_complete_error_locations_and_consistency_improvement():
    reference = read(ROOT / "reproducibility/table3/reference.json")
    bundle = ROOT / "reproducibility/table6"
    manifest = read(bundle / "manifest.json")
    targets = {(r["dataset"], r["row"], r["col"]) for r in reference["targets"]}
    members = {
        (m["table"], m["local_row"], m["col"])
        for group in reference["consistency_groups"]
        for m in group["members"]
    }
    assert len(targets | members) == 1409
    error_tables = {n for n, _, _ in targets | members}
    assert len(error_tables) == 168
    baseline = dict.fromkeys(reference["tables"], "complete")
    _, total = metrics(score_run([], reference, baseline))
    assert (total["tp"], total["fp"], total["fn"]) == (0, 0, 1178)
    for name in manifest["runs"]:
        with gzip.open(bundle / name, "rt") as stream:
            assert set(json.load(stream)["executions"]) == error_tables


def test_rescore_repair_only_outputs():
    rows = reproduce(
        ROOT / "reproducibility/table6", ROOT / "reproducibility/table3/reference.json"
    )
    assert len(rows) == 16
    assert sum(row["repetitions"] for row in rows) == 30
    scores = {row["method"]: row for row in rows}
    assert scores["GPT-5.2 (Prompt-tuned)"]["f1"] == pytest.approx(88.59, abs=0.01)
    assert scores["GPT-6-astra (Prompt-tuned)"]["f1"] == pytest.approx(90.61, abs=0.01)
