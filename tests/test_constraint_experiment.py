"""Check exact native inputs and outcome-independent threshold selection."""

from pathlib import Path

import pytest

from baselines.common import fingerprint, read_json
from baselines.constraints.selection import load, select
from scripts.run_constraint_sweep import build_command, run_command
from src.reproducibility.constraints import cross_fit

ROOT = Path(__file__).resolve().parents[1]


def test_every_exported_native_projection_matches_the_executed_experiment():
    pool, selections = load()
    manifest = read_json(ROOT / "reproducibility/constraints/manifest.json")
    plans = read_json(ROOT / "reproducibility/constraints/plans.json")
    assert sum(map(len, pool["tables"].values())) == 1912
    for bank, methods in manifest["runs"].items():
        for method, record in methods.items():
            actual = {ds: select(pool, selections, method, ds, bank=bank) for ds in pool["tables"]}
            assert fingerprint(actual) == record["native_specs_sha256"]
    for threshold, bank in plans["numeric"].items():
        for method in pool["methods"]:
            for ds in pool["tables"]:
                assert select(pool, selections, method, ds, int(threshold)) == select(
                    pool, selections, method, ds, bank=bank
                )


def test_unknown_native_configuration_is_not_silently_empty():
    pool, selections = load()
    with pytest.raises(ValueError, match="Unknown"):
        select(pool, selections, "not_a_method", "table")
    with pytest.raises(ValueError, match="Unknown"):
        select(pool, selections, "nadeef", "table", bank="unknown")


def count(tp, fp=0):
    return dict(
        tp=tp,
        fp=fp,
        fn=10 - tp,
        fp_clean=0,
        clean_cells=1,
        execution="complete",
        types={
            k: dict(hit=tp, gt=10) for k in ["typo-spelling", "typo-formatting", "inconsistency"]
        },
    )


def test_threshold_choice_uses_other_folds_not_the_evaluation_fold():
    curves = {0: {"a": count(10), "b": count(0)}, 20: {"a": count(0), "b": count(10)}}
    result, choices = cross_fit(curves, [[["a"]], [["b"]]])
    assert choices == [20, 0]
    assert result["f1"] == 0


def test_native_container_receives_dirty_inputs_and_output_mounts_only(tmp_path):
    cmd = run_command("nadeef", tmp_path / "inputs", tmp_path / "output/bank", "bank", 2)
    mounts = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--mount"]
    assert len(mounts) == 2 and mounts[0].endswith("target=/inputs,readonly")
    assert all("reference.json" not in value and "reproducibility" not in value for value in cmd)
    assert "constraint-classical" in build_command("nadeef")
    assert "constraint-uniclean" in build_command("uniclean")
