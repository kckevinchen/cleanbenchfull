"""Select scored constraints and their experiment-verified native projections."""

from pathlib import Path

from baselines.common import read_json, sha

CONFIG = Path(__file__).resolve().parent / "config"


def load():
    for name, expected in read_json(CONFIG / "manifest.json").items():
        if sha(CONFIG / name) != expected:
            raise ValueError(f"Constraint configuration checksum mismatch: {name}")
    return read_json(CONFIG / "pool.json"), read_json(CONFIG / "selections.json")


def select(pool, selections, method, dataset, threshold=0, bank=None):
    if method not in pool["methods"] or threshold not in pool["thresholds"]:
        raise ValueError("Unknown method or threshold")
    if bank is not None and bank not in selections:
        raise ValueError("Unknown saved constraint bank")
    allowed = set(selections[bank].get(dataset, [])) if bank else None
    result = []
    for record in pool["tables"].get(dataset, []):
        chosen = (
            record["rule"]["rule_id"] in allowed
            if allowed is not None
            else record["score"] >= threshold
        )
        if chosen and method in record["native"]:
            result.append(record["native"][method])
    return result
