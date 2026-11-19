"""Typed predicates and exhaustive dirty-table validation; no label access."""

from __future__ import annotations

import collections
import datetime as dt
import itertools
from decimal import Decimal, InvalidOperation

from baselines.common import fingerprint

OPS = {"eq", "ne", "lt", "le", "gt", "ge", "is_null", "not_null"}


def conjunction(values):
    values = list(values)
    return False if False in values else None if None in values else True


def validate_rule(raw, width, cfg):
    if not isinstance(raw, dict):
        raise ValueError("Rule must be an object")
    rule = dict(raw)
    family = rule.get("family")
    fields = {"family", "conditions", "null_tokens", "rationale", "exceptions", "replaces_rule_id"}
    fields |= {
        "fd": {"lhs", "rhs", "null_policy"},
        "key": {"columns", "null_policy", "require_non_null"},
        "dc": {"tuple_count", "predicates"},
    }.get(family, set())
    if family not in {"fd", "key", "dc"} or set(rule) - fields:
        raise ValueError("Unsupported family or fields")
    for name in ["rationale", "exceptions"]:
        if not isinstance(rule.get(name), str) or (name == "rationale" and not rule[name].strip()):
            raise ValueError("Missing semantic rationale/exceptions")
    tokens = rule.get("null_tokens")
    if not isinstance(tokens, list) or any(not isinstance(t, str) for t in tokens):
        raise ValueError("Explicit string null_tokens required")
    rule["null_tokens"] = sorted(set(tokens))
    columns = {f"C{i}" for i in range(width)}

    def column(value):
        if not isinstance(value, str) or value not in columns:
            raise ValueError("Unknown column ID")

    if family in {"fd", "key"}:
        key = "lhs" if family == "fd" else "columns"
        limit = cfg["max_lhs_columns" if family == "fd" else "max_key_columns"]
        seq = rule.get(key)
        if not isinstance(seq, list) or not 1 <= len(seq) <= limit:
            raise ValueError("Invalid determinant/key size")
        for col in seq:
            column(col)
        if len(set(seq)) != len(seq):
            raise ValueError("Repeated key/determinant column")
        rule[key] = sorted(seq, key=lambda c: int(c[1:]))
        if rule.get("null_policy") not in {"literal", "exclude"}:
            raise ValueError("Explicit null policy required")
        if family == "fd":
            column(rule.get("rhs"))
            if rule["rhs"] in seq:
                raise ValueError("Trivial FD")
        elif type(rule.get("require_non_null")) is not bool:
            raise ValueError("Explicit key non-null requirement required")
    else:
        if type(rule.get("tuple_count")) is not int or rule["tuple_count"] not in {1, 2}:
            raise ValueError("Invalid tuple_count")

    def predicate(p):
        if isinstance(p, dict):
            p = {k: v for k, v in p.items() if v is not None}
        if not isinstance(p, dict) or set(p) - {"op", "left", "right", "type", "date_format"}:
            raise ValueError("Invalid predicate schema")
        op, typ = p.get("op"), p.get("type")
        if op not in OPS or typ not in {"string", "number", "date"}:
            raise ValueError("Unsupported predicate operator/type")
        if typ == "string" and op not in {"eq", "ne", "is_null", "not_null"}:
            raise ValueError("String ordering is unsupported")
        if typ == "date":
            if not isinstance(p.get("date_format"), str) or not p["date_format"]:
                raise ValueError("Date format required")
            # Reject formats without year/month/day to avoid implicit calendar defaults.
            fmt = p["date_format"]
            if (
                not any(x in fmt for x in ["%Y", "%y"])
                or "%d" not in fmt
                or not any(x in fmt for x in ["%m", "%b", "%B"])
            ):
                raise ValueError("Date requires explicit year, month and day")
        elif "date_format" in p:
            raise ValueError("Date format on non-date predicate")
        for side in ["left"] + ([] if op in {"is_null", "not_null"} else ["right"]):
            ref = p.get(side)
            if not isinstance(ref, dict):
                raise ValueError("Predicate operand missing")
            if side == "right" and set(ref) == {"value"} and isinstance(ref["value"], str):
                continue
            if set(ref) != {"row", "column"}:
                raise ValueError("Invalid column operand")
            if ref["row"] not in ({"t1", "t2"} if rule.get("tuple_count") == 2 else {"t1"}):
                raise ValueError("Invalid tuple reference")
            column(ref["column"])
        if op in {"is_null", "not_null"} and "right" in p:
            raise ValueError("Unary null check has right operand")
        return dict(p)

    if not isinstance(rule.get("conditions"), list):
        raise ValueError("Explicit conditions list required")
    rule["conditions"] = [predicate(p) for p in rule["conditions"]]
    ps = rule.get("predicates", [])
    if family == "dc":
        if not isinstance(ps, list) or not ps:
            raise ValueError("DC predicates required")
        rule["predicates"] = [predicate(p) for p in ps]
    if len(ps) + len(rule["conditions"]) > cfg["max_predicates"]:
        raise ValueError("Predicate bound exceeded")
    if family == "dc" and rule["tuple_count"] == 2:
        roles = {
            v["row"]
            for p in ps + rule["conditions"]
            for v in [p["left"], p.get("right", {})]
            if "row" in v
        }
        if roles != {"t1", "t2"}:
            raise ValueError("Two-row DC must reference both tuples")
    semantic = {
        k: v for k, v in rule.items() if k not in {"rationale", "exceptions", "replaces_rule_id"}
    }
    for k in ["conditions", "predicates"]:
        if k in semantic:
            semantic[k] = sorted(semantic[k], key=fingerprint)
    rule["rule_id"] = family + "_" + fingerprint(semantic)[:20]
    return rule


def pred_value(p, rows, indices, null_tokens):
    def value(ref):
        if "value" in ref:
            return ref["value"]
        return rows[indices[int(ref["row"][1:]) - 1]][int(ref["column"][1:])]

    a = value(p["left"])
    op = p["op"]
    if op in {"is_null", "not_null"}:
        return (a in null_tokens) == (op == "is_null")
    b = value(p["right"])
    if a in null_tokens or b in null_tokens:
        return None
    try:
        if p["type"] == "number":
            a, b = Decimal(a), Decimal(b)
            if not a.is_finite() or not b.is_finite():
                return None
        elif p["type"] == "date":
            a, b = (dt.datetime.strptime(v, p["date_format"]) for v in [a, b])
        return {
            "eq": lambda: a == b,
            "ne": lambda: a != b,
            "lt": lambda: a < b,
            "le": lambda: a <= b,
            "gt": lambda: a > b,
            "ge": lambda: a >= b,
        }[op]()
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def evaluate(rule, rows, cfg):
    tokens = set(rule["null_tokens"])
    family = rule["family"]
    out = {"rule_id": rule["rule_id"], "applicable": 0, "unknown": 0, "violations": 0}
    examples, cells = [], set()
    if family == "dc":
        pairs = (
            itertools.permutations(range(len(rows)), 2)
            if rule["tuple_count"] == 2
            else ((i,) for i in range(len(rows)))
        )
        for ix in pairs:
            cond = conjunction(pred_value(p, rows, ix, tokens) for p in rule["conditions"])
            if cond is False:
                continue
            if cond is None:
                out["unknown"] += 1
                continue
            out["applicable"] += 1
            result = conjunction(pred_value(p, rows, ix, tokens) for p in rule["predicates"])
            if result is None:
                out["unknown"] += 1
            elif result:
                out["violations"] += 1
                examples.append(list(ix))
                for p in rule["conditions"] + rule["predicates"]:
                    for ref in [p["left"], p.get("right", {})]:
                        if "column" in ref:
                            cells.add((ix[int(ref["row"][1:]) - 1], int(ref["column"][1:])))
        out["pair_semantics"] = "ordered_distinct" if rule["tuple_count"] == 2 else "single_row"
    else:
        cols = [int(c[1:]) for c in rule["lhs" if family == "fd" else "columns"]]
        rhs = int(rule["rhs"][1:]) if family == "fd" else None
        involved = cols + ([rhs] if rhs is not None else [])
        groups = collections.defaultdict(list)
        null_rows, applicable_rows = [], []
        for i, row in enumerate(rows):
            cond = conjunction(pred_value(p, rows, (i,), tokens) for p in rule["conditions"])
            if cond is None:
                out["unknown"] += 1
            if cond is not True:
                continue
            if any(row[j] in tokens for j in involved):
                null_rows.append(i)
                if rule["null_policy"] == "exclude":
                    continue
            groups[tuple(row[j] for j in cols)].append(i)
            applicable_rows.append(i)
        out["applicable"] = len(applicable_rows)
        out["null_rows"] = null_rows
        out["groups"] = len(groups)
        out["repeated_group_rows"] = sum(len(ix) for ix in groups.values() if len(ix) > 1)
        out["conflicting_groups"] = 0
        candidates = []
        if family == "fd":
            out["conflicting_pairs"] = 0
            for key, ix in groups.items():
                freq = collections.Counter(rows[i][rhs] for i in ix)
                if len(freq) < 2:
                    continue
                top = max(freq.values())
                modes = sorted(v for v, n in freq.items() if n == top)
                out["violations"] += len(ix) - top
                out["conflicting_pairs"] += (len(ix) ** 2 - sum(n * n for n in freq.values())) // 2
                out["conflicting_groups"] += 1
                cells.update((i, j) for i in ix for j in involved)
                examples.append(ix)
                candidates.append(
                    {
                        "rows": ix,
                        "lhs_values": key,
                        "rhs_frequencies": dict(freq),
                        "modal_values": modes,
                        "unique_mode": len(modes) == 1,
                    }
                )
            out["agreement"] = (
                1 - out["violations"] / len(applicable_rows) if applicable_rows else None
            )
            out["candidate_groups"] = candidates
        else:
            for ix in groups.values():
                if len(ix) > 1:
                    out["violations"] += len(ix) - 1
                    out["conflicting_groups"] += 1
                    cells.update((i, j) for i in ix for j in cols)
                    examples.append(ix)
            out["non_null_violations"] = len(null_rows) if rule["require_non_null"] else 0
            if out["non_null_violations"]:
                cells.update((i, j) for i in null_rows for j in cols if rows[i][j] in tokens)
            out["empirically_unique"] = bool(applicable_rows) and out["violations"] == 0
            out["empirically_minimal"] = out["empirically_unique"] and all(
                len({tuple(rows[i][j] for j in sub) for i in applicable_rows})
                < len(applicable_rows)
                for size in range(1, len(cols))
                for sub in itertools.combinations(cols, size)
            )
    out["violation_examples"] = examples[: cfg["examples_per_rule"]]
    out["violation_instances"] = examples
    out["participating_cells"] = sorted(cells)
    out["observed_status"] = (
        "violated"
        if out["violations"] or out.get("non_null_violations")
        else "unknown"
        if out["unknown"]
        else "vacuous"
        if not out["applicable"] or (family == "fd" and not out.get("repeated_group_rows"))
        else "satisfied"
    )
    return out


def validate_candidates(candidates, rows, cfg):
    accepted, invalid, seen = [], [], set()
    for raw in candidates:
        try:
            rule = validate_rule(raw, len(rows[0]), cfg)
            if rule["rule_id"] in seen:
                invalid.append({"candidate": raw, "reason": "duplicate_semantic_rule"})
                continue
            seen.add(rule["rule_id"])
            accepted.append({"rule": rule, "validation": evaluate(rule, rows, cfg)})
        except (ValueError, TypeError, KeyError) as exc:
            invalid.append({"candidate": raw, "reason": str(exc)})
    return {"valid": accepted, "invalid": invalid}


def review_view(record):
    validation = {
        k: v
        for k, v in record["validation"].items()
        if k not in {"participating_cells", "violation_instances", "candidate_groups", "null_rows"}
    }
    return {"rule": record["rule"], "validation": validation}


def logical_audit(records):
    """Bounded exact-equivalence and numeric-bound checks, not a SAT proof."""
    equivalent, bounds, conflicts = {}, collections.defaultdict(list), []
    for record in records:
        r = record["rule"]
        if r["conditions"]:
            continue
        form = None
        if r["family"] == "fd" and r["null_policy"] == "literal":
            form = ("fd", tuple(r["lhs"]), r["rhs"])
        elif r["family"] == "key" and r["null_policy"] == "literal" and not r["require_non_null"]:
            form = ("key", tuple(r["columns"]))
        elif r["family"] == "dc" and not r["null_tokens"] and r["tuple_count"] == 2:
            equal, unequal, usable = [], [], True
            for p in r["predicates"]:
                a, b = p["left"], p.get("right", {})
                if (
                    p["type"] != "string"
                    or p["op"] not in {"eq", "ne"}
                    or "column" not in b
                    or a["column"] != b["column"]
                    or {a["row"], b.get("row")} != {"t1", "t2"}
                ):
                    usable = False
                    break
                (equal if p["op"] == "eq" else unequal).append(a["column"])
            if usable and equal and len(unequal) <= 1:
                seq = tuple(sorted(set(equal), key=lambda c: int(c[1:])))
                form = ("fd", seq, unequal[0]) if unequal else ("key", seq)
        if form:
            equivalent.setdefault(form, []).append(r["rule_id"])
        if r["family"] == "dc" and r["tuple_count"] == 1 and len(r["predicates"]) == 1:
            p = r["predicates"][0]
            if (
                p["type"] == "number"
                and p["op"] in {"gt", "ge", "lt", "le"}
                and "value" in p.get("right", {})
            ):
                try:
                    number = Decimal(p["right"]["value"])
                    if number.is_finite():
                        bounds[p["left"]["column"]].append((p["op"], number, r["rule_id"]))
                except InvalidOperation:
                    pass
    for column, bb in bounds.items():
        for low in [b for b in bb if b[0] in {"lt", "le"}]:
            for high in [b for b in bb if b[0] in {"gt", "ge"}]:
                if low[1] > high[1] or (low[1] == high[1] and (low[0] == "le" or high[0] == "ge")):
                    conflicts.append(
                        {
                            "column": column,
                            "rule_ids": [low[2], high[2]],
                            "reason": "No finite non-null number satisfies both bounds; null/unknown values may remain outside evaluation",
                        }
                    )
    return {
        "exact_equivalences": [ids for ids in equivalent.values() if len(ids) > 1],
        "possible_conflicts": conflicts,
        "scope": "Unconditional literal-null FD/key and empty-null-token equality DC equivalence; single-predicate numeric bounds. Not complete logical consistency checking.",
    }
