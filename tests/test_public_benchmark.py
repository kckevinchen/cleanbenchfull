"""Verify scorer immunity and detection/correction semantics on synthetic data."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCORER = Path(__file__).resolve().parents[1] / "benchmark/eval_cleanbench.py"


def write_jsonl(path, records):
    path.write_text("".join(json.dumps(r) + "\n" for r in records))


def scorer_module():
    spec = importlib.util.spec_from_file_location("public_scorer", SCORER)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_skip_immunity_does_not_mask_a_row(tmp_path):
    base = {"dataset": "pbi_500", "source": "x.csv"}
    write_jsonl(
        tmp_path / "benchmark_cell.json", [{**base, "row": 0, "column": "name", "status": "error"}]
    )
    write_jsonl(tmp_path / "ignored.jsonl", [])
    write_jsonl(
        tmp_path / "neutral_cells.jsonl",
        [
            {"kind": "skip", "source": "x.csv", "row": 0, "column": "city"},
            {"kind": "skip", "source": "x.csv", "row": 1, "column": ""},
            {"kind": "exclude_csv", "source": "x.csv"},
        ],
    )
    index = scorer_module()._load_ignore_index(tmp_path / "ignored.jsonl")
    assert index.contains(("pbi_500", "x.csv", 0, "city"))
    assert not index.contains(("pbi_500", "x.csv", 0, "name"))
    assert not index.contains(("pbi_500", "x.csv", 1, "name"))


def test_detection_and_wrong_correction(tmp_path):
    base = {"dataset": "pbi_500", "source": "x.csv", "column": "name"}
    write_jsonl(
        tmp_path / "benchmark_cell.json",
        [
            {**base, "row": 0, "status": "error", "corrected_value": "Alex"},
            {**base, "row": 1, "status": "ambiguous", "corrected_value": None},
        ],
    )
    write_jsonl(tmp_path / "ignored.jsonl", [{**base, "row": 2, "scope": "rows"}])
    write_jsonl(
        tmp_path / "predictions.jsonl",
        [
            {**base, "row": 0, "corrected_value": "Wrong"},
            {**base, "row": 1},
            {**base, "row": 2},
        ],
    )
    for task, counts in [("detection", (1, 0, 0)), ("end_to_end", (0, 1, 1))]:
        result = subprocess.run(
            [
                sys.executable,
                str(SCORER),
                "--benchmark-dir",
                str(tmp_path),
                "--predictions",
                str(tmp_path / "predictions.jsonl"),
                "--task",
                task,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        for key, expected in zip(("TP", "FP", "FN"), counts):
            assert f"{key}: {expected}\n" in result.stdout


@pytest.mark.parametrize(
    ("task", "edit", "execution", "expected"),
    [
        ("end_to_end", None, None, (2, 0, 0)),
        ("end_to_end", "wrong", None, (1, 1, 1)),
        ("end_to_end", "New York", None, (2, 0, 0)),
        ("end_to_end", None, "failed", (1, 0, 1)),
        ("end_to_end", None, "unknown", (1, 0, 1)),
        ("detection", None, None, (1, 0, 1)),
    ],
)
def test_all_errors_include_accepted_unchanged_values(tmp_path, task, edit, execution, expected):
    base = {"dataset": "pbi_500", "source": "x.csv", "column": "city"}
    write_jsonl(
        tmp_path / "benchmark_cell.json",
        [
            {
                **base,
                "row": 0,
                "status": "error",
                "original_value": "NY",
                "corrected_value": ["NY", "New York"],
            },
            {
                **base,
                "row": 1,
                "status": "error",
                "original_value": "Seatle",
                "corrected_value": "Seattle",
            },
        ],
    )
    write_jsonl(tmp_path / "ignored.jsonl", [])
    predictions = [{**base, "row": 1, "corrected_value": "Seattle"}]
    if edit is not None:
        predictions.append({**base, "row": 0, "corrected_value": edit})
    write_jsonl(tmp_path / "predictions.jsonl", predictions)
    command = [
        sys.executable,
        str(SCORER),
        "--benchmark-dir",
        str(tmp_path),
        "--predictions",
        str(tmp_path / "predictions.jsonl"),
        "--task",
        task,
    ]
    if execution:
        (tmp_path / "execution.json").write_text(json.dumps({"x": execution}))
        command.extend(["--execution", str(tmp_path / "execution.json")])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    for key, count in zip(("TP", "FP", "FN"), expected):
        assert f"{key}: {count}\n" in result.stdout
