"""Scope-preserving transport to released UniClean cleaners and native search.

No constraint directly overwrites a cell. Pattern/date/number preprocessing,
FD/reference-domain candidate generation, edit costs, and selection are native.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


class ReferenceDomain(dict):
    """Adapt native tuple-membership and column-list reference APIs together."""

    def __init__(self, column, values):
        super().__init__({column: list(values)})
        self.allowed = set(values)

    def __contains__(self, key):
        if isinstance(key, tuple):
            return len(key) == 1 and key[0] in self.allowed
        return super().__contains__(key)


def scope_check(conditions, tokens, skip_column=None):
    from baselines.constraints.rules import conjunction, pred_value

    columns = {p["left"]["column"] for p in conditions}
    columns.update(p["right"]["column"] for p in conditions if "column" in p.get("right", {}))
    if skip_column:
        columns.add(skip_column)
    columns = sorted(columns)

    def valid(value, frame):
        data = value.to_dict() if hasattr(value, "to_dict") else {columns[0]: value}
        row = [""] * (1 + max((int(c[1:]) for c in columns), default=0))
        for c, v in data.items():
            row[int(c[1:])] = str(v)
        if skip_column and row[int(skip_column[1:])] in tokens:
            return False
        return conjunction(pred_value(p, [row], (0,), set(tokens)) for p in conditions) is True

    return columns, valid


def execute(recipe, rows):
    from pyspark.sql import SparkSession
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType, StructField, StructType

    root = Path(recipe["project"])
    source = root / "external/uniclean/upstream/uniclean_cleaners"
    sys.path.insert(0, str(source))
    os.environ["MPLBACKEND"] = "Agg"
    import AnalyticsCache.get_planuml_graph as graph

    graph.PlantUML.process_str = lambda *a, **k: None
    graph.PlantUML.process_file = lambda *a, **k: None
    from AnalyticsCache.handle_rules import transformRulesToSpark
    from Clean import CleanonLocalWithnoSmple
    from SampleScrubber.cleaner.multiple import AttrRelation, Predicate
    from SampleScrubber.cleaner.single import Date, DisguisedMissHandler, Number, Pattern
    from SampleScrubber.param_selector import customclean

    work = Path(recipe["work"])
    work.mkdir(parents=True, exist_ok=False)
    cols = [f"C{i}" for i in range(len(rows[0]))]
    schema = StructType([StructField(c, StringType(), False) for c in ["index", *cols]])
    spark = (
        SparkSession.builder.master("local[1]")
        .appName("UniClean-rich-shared")
        .config("spark.driver.memory", "2g")
        .config("spark.sql.shuffle.partitions", "1")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    try:
        data = spark.createDataFrame([[str(i), *row] for i, row in enumerate(rows)], schema)
        signals = []
        for n, spec in enumerate(recipe["specs"]):
            kind = spec["kind"]
            if kind in {"fd", "conditional_fd", "domain"}:
                cond = spec.get("conditions", [])
                tokens = spec.get("null_tokens", [])
                if kind == "domain":
                    column = spec["column"]
                    lhs = [column]
                    rhs = column
                    reference = ReferenceDomain(column, spec["values"])
                    scope_cols, check = scope_check(cond, tokens, column)
                else:
                    lhs = list(spec["fd"]["lhs"])
                    rhs = spec["fd"]["rhs"]
                    reference = None
                    scope_cols, check = scope_check(cond, tokens)
                lhs = list(dict.fromkeys(lhs + scope_cols))
                signal = AttrRelation(
                    lhs,
                    [rhs],
                    str(n),
                    Cpredicate_list=[Predicate(scope_cols, check)] if scope_cols else None,
                    ExtDict=reference,
                )
                if kind == "domain":
                    # Pure unary reference checks have no dependency-graph edge.
                    # Invoke the released selector directly so they are executed.
                    frame = data.select("index", *cols).toPandas()
                    native = customclean(frame, cleaners=[signal])
                    edits = native[2]
                    if edits:
                        data, _ = transformRulesToSpark(edits, data, batch_size=500)
                else:
                    signals.append(signal)
                continue
            column = spec["column"]
            cond = spec.get("conditions", [])
            tokens = spec.get("null_tokens", [])
            scope_cols, check = scope_check(cond, tokens, column if kind != "not_null" else None)
            # Evaluate the declarative scope using dirty/current values, never a GT mask.
            frame = data.select("index", *cols).toPandas()
            eligible = []
            for _, r in frame.iterrows():
                value = r[scope_cols] if scope_cols else None
                if not scope_cols or check(value, frame):
                    eligible.append(str(r["index"]))
            if not eligible:
                continue
            subset = data.filter(F.col("index").isin(eligible))
            other = data.filter(~F.col("index").isin(eligible))
            frame = subset.select(column).distinct().toPandas()
            if kind == "pattern":
                cleaner = Pattern(column, spec["pattern"], str(n))
            elif kind == "date":
                cleaner = Date(column, spec["format"], str(n))
            elif kind == "number":
                cleaner = Number(
                    column,
                    [
                        float(spec["minimum"]) if spec["minimum"] is not None else float("-inf"),
                        float(spec["maximum"]) if spec["maximum"] is not None else float("inf"),
                    ],
                    str(n),
                )
            elif kind == "not_null":
                cleaner = DisguisedMissHandler(
                    column, name=str(n), missing_values=spec["null_tokens"]
                )
            else:
                raise ValueError("Unknown native scalar cleaner")
            result = customclean(frame, precleaners=[cleaner])
            edits = result[2]
            if edits:
                subset, _ = transformRulesToSpark(edits, subset, batch_size=500)
            data = subset.unionByName(other)
        if signals:
            data = CleanonLocalWithnoSmple(spark, signals, data, str(work), single_max=10000)
        output = data.select("index", *cols).collect()
        by_id = {}
        for record in output:
            i = int(record["index"])
            if i in by_id or not 0 <= i < len(rows):
                raise ValueError("UniClean changed row identity")
            by_id[i] = [record[c] for c in cols]
        if set(by_id) != set(range(len(rows))) or any(
            not isinstance(v, str) for row in by_id.values() for v in row
        ):
            raise ValueError("UniClean changed source shape or emitted non-string output")
        changes = [
            dict(row=i, col=j, corrected_value=v)
            for i, row in by_id.items()
            for j, v in enumerate(row)
            if v != rows[i][j]
        ]
        return dict(changes=changes, detections=[dict(row=c["row"], col=c["col"]) for c in changes])
    finally:
        spark.stop()
