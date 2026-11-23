"""Cell-distance scoring for jointly constrained inconsistency components."""

from __future__ import annotations

import itertools
import re
from collections import defaultdict
from copy import deepcopy


def cell(m):
    return m["table"], m["local_row"], m["col"]


def style_phone(v):
    if re.fullmatch(r"\(\d{3}\) \d{3}-\d{4}", v):
        return "parentheses"
    if re.fullmatch(r"\d{3}-\d{3}-\d{4}", v):
        return "hyphens"
    return None


def style_fund(v):
    if re.fullmatch(r".+ - Class [A-Za-z0-9]+", v):
        return "spaced_dash"
    if re.fullmatch(r".+[^ ]-Class [A-Za-z0-9]+", v):
        return "compact_dash"
    return None


def title_style(v):
    words = re.findall(r"[A-Za-z]+", v)
    return bool(words) and all(w[0].isupper() and (len(w) == 1 or not w.isupper()) for w in words)


def choices(group, targets, intersect_targets=True):
    rule = group["scoring_rule"]
    options = group.get("canonical_options") or group.get("format_options") or ["title"]
    output = []
    for option in options:
        candidate = {}
        for m in group["members"]:
            allowed = set(m["accepted"])
            if intersect_targets and cell(m) in targets:
                allowed &= targets[cell(m)]
            if rule == "literal_unification":
                allowed &= {option}
            elif rule == "phone_format":
                allowed = {
                    v
                    for v in allowed
                    if style_phone(v) == option
                    and re.sub(r"\D", "", v) == re.sub(r"\D", "", m["original_value"])
                }
            elif rule == "fund_class_separator":
                entity = lambda v: re.sub(r"\s*-\s*Class\s+", "|Class|", v)
                allowed = {
                    v
                    for v in allowed
                    if style_fund(v) == option and entity(v) == entity(m["original_value"])
                }
            elif rule == "title_case_names":
                allowed = {
                    v
                    for v in allowed
                    if title_style(v) and v.casefold() == m["original_value"].casefold()
                }
            else:
                raise ValueError(rule)
            if not allowed:
                break
            candidate[cell(m)] = allowed
        else:
            output.append(candidate)
    assert output, group["id"]
    return output


def components(groups, targets, intersect_targets=True):
    parent = list(range(len(groups)))

    def find(i):
        if parent[i] != i:
            parent[i] = find(parent[i])
        return parent[i]

    owner = {}
    for i, g in enumerate(groups):
        for m in g["members"]:
            c = cell(m)
            if c in owner:
                parent[find(i)] = find(owner[c])
            owner[c] = i
    sets = defaultdict(list)
    for i in range(len(groups)):
        sets[find(i)].append(i)
    result = []
    for ids in sets.values():
        legal = []
        for combination in itertools.product(
            *(choices(groups[i], targets, intersect_targets) for i in ids)
        ):
            joint = {}
            for alternative in combination:
                for c, allowed in alternative.items():
                    joint[c] = joint[c] & allowed if c in joint else set(allowed)
            if all(joint.values()):
                legal.append(joint)
        assert legal, ids
        original = {cell(m): m["original_value"] for i in ids for m in groups[i]["members"]}
        assert all(set(state) == set(original) for state in legal)
        d0 = distance(original, legal)
        assert d0 > 0, ids
        result.append(dict(ids=ids, legal=legal, original=original, d0=d0))
    return result


def distance(values, legal):
    return min(
        sum(values[c] not in allowed for c, allowed in alternative.items()) for alternative in legal
    )


def score_tables(counts, predictions, targets, types, joint):
    """Replace group member counts with improvement; preserve other cell scoring.

    Type recalls are diagnostic: spelling/formatting keep accepted-value
    matching; inconsistency replaces its grouped cells with repair distance.
    Overall counts remove every overlapping target before adding group benefit.
    """
    result = deepcopy(counts)
    pred = {
        (p["dataset"], p["row"], p["col"]): p["corrected_value"]
        for p in predictions
        if isinstance(p.get("corrected_value"), str)
    }
    for component in joint:
        original, legal = component["original"], component["legal"]
        names = {c[0] for c in original}
        if len(names) != 1:
            raise ValueError("Table-level scoring requires components within one table")
        row = result[next(iter(names))]
        old_targets = original.keys() & targets.keys()
        assert old_targets <= types["inconsistency"]
        old_hits = sum(c in pred and pred[c] in targets[c] for c in old_targets)
        old_wrong = sum(c in pred and pred[c] not in targets[c] for c in old_targets)
        after = {c: pred.get(c, value) for c, value in original.items()}
        before = component["d0"]
        remaining = distance(after, legal) if row["execution"] == "complete" else before
        gain = max(0, before - remaining)
        invalid = sum(
            c in pred and not any(pred[c] in state[c] for state in legal) for c in original
        )
        row["tp"] += gain - old_hits
        row["fn"] += (before - gain) - (len(old_targets) - old_hits)
        row["fp"] += invalid - old_wrong
        row["types"]["inconsistency"]["hit"] += gain - old_hits
        row["types"]["inconsistency"]["gt"] += before - len(old_targets)
        assert all(row[k] >= 0 for k in ("tp", "fp", "fn"))
    return result
