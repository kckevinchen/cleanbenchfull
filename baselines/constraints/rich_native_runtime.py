"""Isolated native execution for shared rich constraint plans; no GT inputs."""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import subprocess
import time
import traceback
import xml.etree.ElementTree as ET
from pathlib import Path

from baselines.common import NATIVE_ROOT as ROOT
from baselines.common import atomic_json, fingerprint, read_json, sha


def java_string(value):
    return json.dumps(value, ensure_ascii=True)


def predicate_builder(p):
    if p["op"] not in {"eq", "ne"} or p["type"] != "string":
        raise ValueError("NADEEF VARCHAR predicates require raw string EQ/NEQ")
    left = 'new Column("INPUT_DATA", ' + java_string(p["left"]["column"]) + ")"
    expression = (
        "new Predicate.PredicateBuilder().left("
        + left
        + ").op(Operation."
        + {"eq": "EQ", "ne": "NEQ"}[p["op"]]
        + ")"
    )
    if "value" in p["right"]:
        expression += ".constant(" + java_string(p["right"]["value"]) + ").isSingle()"
    else:
        expression += '.right(new Column("INPUT_DATA", ' + java_string(p["right"]["column"]) + "))"
        if p["right"]["row"] == "t1":
            expression += ".isSingle()"
    return expression + ".build()"


def nadeef_template(name, predicates, tuple_count):
    base = ROOT / "external/adr/adr_repo/NADEEF-master/ruleext/src/qa/qcri/nadeef/ruleext/template"
    path = base / ("SingleDCRuleBuilder.stg" if tuple_count == 1 else "PairDCRuleBuilder.stg")
    text = path.read_text().split("dcTemplate(", 1)[1].split("::= <<", 1)[1].rsplit(">>", 1)[0]
    return (
        text.replace("$DCName$", name)
        .replace("$tableName$", "INPUT_DATA")
        .replace(
            "$template:addPredicate()$",
            "\n".join("predicates.add(" + predicate_builder(p) + ");" for p in predicates),
        )
    )


def nadeef_cfd(name, spec):
    path = (
        ROOT
        / "external/adr/adr_repo/NADEEF-master/ruleext/src/qa/qcri/nadeef/ruleext/template/PairCFDRuleBuilder.stg"
    )
    text = path.read_text().split("cfdTemplate(", 1)[1].split("::= <<", 1)[1].rsplit(">>", 1)[0]
    lhs = list(dict.fromkeys(spec["fd"]["lhs"] + [p["left"]["column"] for p in spec["conditions"]]))
    return (
        text.replace("$CFDName$", name)
        .replace(
            "$leftHandSide:leftItem()$",
            "\n".join('lhs.add(new Column("INPUT_DATA", ' + java_string(c) + "));" for c in lhs),
        )
        .replace(
            "$rightHandSide:rightItem()$",
            'rhs.add(new Column("INPUT_DATA", ' + java_string(spec["fd"]["rhs"]) + "));",
        )
        .replace(
            "$lExpression$",
            "\n".join(
                "leftFilterExpressions.add(" + predicate_builder(p) + ");"
                for p in spec["conditions"]
            ),
        )
    )


def run_nadeef(recipe, rows):
    work = Path(recipe["work"])
    work.mkdir(parents=True, exist_ok=False)
    classes = work / "classes"
    classes.mkdir()
    entries, files = [], []
    source = dict(
        type="derbymemory", url="memory:cleanbench;create=true", username="APP", password="APP"
    )
    for i, spec in enumerate(recipe["specs"]):
        if spec["kind"] == "fd":
            fd = spec["fd"]
            entries.append(
                dict(
                    name=f"FD{i}",
                    type="fd",
                    table=["INPUT_DATA"],
                    target=["INPUT_DATA"],
                    value=[",".join(fd["lhs"]) + "|" + fd["rhs"]],
                )
            )
            continue
        definitions = []
        if spec["kind"] == "conditional_fd":
            name = f"OracleCFD{i}"
            definitions.append((name, nadeef_cfd(name, spec)))
        else:
            clauses = spec["clauses"] if spec["kind"] == "dc_clauses" else [spec["predicates"]]
            for j, ps in enumerate(clauses):
                name = f"OracleDC{i}_{j}"
                definitions.append((name, nadeef_template(name, ps, spec.get("tuple_count", 1))))
        for name, text in definitions:
            path = classes / (name + ".java")
            path.write_text(text)
            files.append(path)
            entries.append(
                dict(
                    name=name, type="udf", table=["INPUT_DATA"], target=["INPUT_DATA"], value=[name]
                )
            )
    runtime = ROOT / "external/nadeef"
    java = runtime / "mamba_env/bin/java"
    cp = str(runtime / "build/bin/*") + ":" + str(runtime / "build/adapter") + ":" + str(classes)
    if files:
        with (work / "compile.log").open("w") as log:
            subprocess.run(
                [
                    str(java.with_name("javac")),
                    "-J-Xmx256m",
                    "-encoding",
                    "UTF-8",
                    "-cp",
                    cp,
                    "-d",
                    str(classes),
                    *map(str, files),
                ],
                cwd=work,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
                timeout=60,
            )
    atomic_json(work / "input.json", dict(rows=rows, n_columns=len(rows[0]), n_rules=len(entries)))
    atomic_json(work / "plan.json", dict(source=source, rule=entries))
    (work / "out").mkdir()
    (work / "nadeef.conf").write_text(
        "database.url=memory:cleanbench;create=true\ndatabase.type=derbymemory\ndatabase.username=APP\ndatabase.password=APP\ngeneral.outputPath=out\ngeneral.alwaysCompile=true\n"
        + f"general.maxIterationNumber={recipe['settings']['nadeef_max_iterations']}\nruleext.fd=qa.qcri.nadeef.ruleext.FDRuleBuilder\n"
    )
    with (work / "native.log").open("w") as log:
        subprocess.run(
            [
                str(java),
                "-Dfile.encoding=UTF-8",
                "-Xmx2g",
                "-cp",
                cp,
                "NadeefBatch",
                "input.json",
                "nadeef.conf",
                "plan.json",
                "native.json",
            ],
            cwd=work,
            env={**os.environ, "JAVA_HOME": str(runtime / "mamba_env"), "TZ": "UTC"},
            stdout=log,
            stderr=subprocess.STDOUT,
            check=True,
            timeout=recipe["settings"]["native_timeout_seconds"],
        )
    from baselines.native.external.nadeef.native import predictions

    result = read_json(work / "native.json")
    export = predictions(result, rows, "", "")
    return dict(
        changes=[
            {k: v for k, v in p.items() if k not in {"dataset", "detector"}} for p in export["e2e"]
        ],
        detections=[dict(row=p["row"], col=p["col"]) for p in export["detection"]],
        iterations=result["iterations"],
    )


def chase_literal(value):
    return json.dumps(value, ensure_ascii=False)


def chase_predicate(p, reference=None):
    if p["op"] not in {"eq", "ne"} or p["type"] != "string":
        raise ValueError("Chase comparison not supported")
    left = "$" + p["left"]["row"] + p["left"]["column"]
    right = (
        (reference(p["right"]["value"]) if reference else chase_literal(p["right"]["value"]))
        if "value" in p["right"]
        else "$" + p["right"]["row"] + p["right"]["column"]
    )
    return left + (" == " if p["op"] == "eq" else " != ") + right


def chase_dependencies(specs, width, constant_sources=None):
    egds, dcs = [], []

    def record(side):
        return (
            "records("
            + ", ".join(["rid: $" + side + "rid"] + [f"C{j}: ${side}C{j}" for j in range(width)])
            + ")"
        )

    for i, spec in enumerate(specs):
        constants = {}

        def reference(value):
            if value not in constants:
                constants[value] = len(constants)
            return f"$r{i}v{constants[value]}"

        def render(p):
            return chase_predicate(p, reference if constant_sources is not None else None)

        def append(target, text):
            if constants:
                name = f"oracle_ref{i}"
                atom = (
                    name + "(" + ", ".join(f"v{j}: $r{i}v{j}" for j in range(len(constants))) + ")"
                )
                text = text.replace(": ", ": " + atom + ", ", 1)
                old = next((r for r in constant_sources if r["name"] == name), None)
                if old is None:
                    old = dict(name=name, values=[])
                    constant_sources.append(old)
                old["values"] = list(constants)
            target.append(text)

        kind = spec["kind"]
        if kind == "chase_egd":
            append(
                egds,
                f"cfd{i}: "
                + ", ".join([record("t1")] + [render(p) for p in spec["conditions"]])
                + " -> "
                + render(spec["head"])
                + ".",
            )
            continue
        if kind in {"fd", "conditional_fd"}:
            fd = spec["fd"]
            body = [record("t1"), record("t2")]
            body += [f"$t1{c} == $t2{c}" for c in fd["lhs"]]
            for p in spec.get("conditions", []):
                body.append(render(p))
                other = copy.deepcopy(p)
                other["left"]["row"] = "t2"
                body.append(render(other))
            append(egds, f"fd{i}: " + ", ".join(body) + f" -> $t1{fd['rhs']} == $t2{fd['rhs']}.")
            continue
        clauses = spec["clauses"] if kind == "chase_clauses" else [spec["predicates"]]
        tuples = spec.get("tuple_count", 1)
        for j, ps in enumerate(clauses):
            body = [record("t1")] + ([record("t2"), "$t1rid != $t2rid"] if tuples == 2 else [])
            # Exactly one disequality is an equality-generating dependency.
            neq = [p for p in ps if p["op"] == "ne"]
            if len(neq) == 1 and tuples == 1:
                head = copy.deepcopy(neq[0])
                head["op"] = "eq"
                append(
                    egds,
                    f"cfd{i}_{j}: "
                    + ", ".join(body + [render(p) for p in ps if p is not neq[0]])
                    + " -> "
                    + render(head)
                    + ".",
                )
            else:
                append(
                    dcs, f"dc{i}_{j}: " + ", ".join(body + [render(p) for p in ps]) + " -> #fail."
                )
    return "ExtEGDs:\n" + "\n".join(egds) + ("\nDCs:\n" + "\n".join(dcs) if dcs else "")


def run_llunatic(recipe, rows):
    from baselines.native.external.llunatic.run import decode, scenario

    work = Path(recipe["work"])
    work.mkdir(parents=True, exist_ok=False)
    path = scenario(rows, [], work)
    document = ET.parse(path)
    constants = []
    document.getroot().find("dependencies").text = chase_dependencies(
        recipe["specs"], len(rows[0]), constants
    )
    if constants:
        source = ET.Element("source")
        ET.SubElement(source, "type").text = "GENERATE"
        schema = "SCHEMA:\n" + "\n".join(
            t["name"] + "(" + ", ".join(f"v{i}" for i in range(len(t["values"]))) + ")"
            for t in constants
        )
        data = "\nINSTANCE:\n" + "\n".join(
            t["name"]
            + "("
            + ", ".join(f"v{i}: " + chase_literal("placeholder") for i, v in enumerate(t["values"]))
            + ")"
            for t in constants
        )
        ET.SubElement(source, "generate").text = schema + data
        document.getroot().insert(0, source)
        authoritative = ET.SubElement(document.getroot(), "authoritativeSources")
        for t in constants:
            ET.SubElement(authoritative, "source").text = t["name"]
    document.write(path, encoding="utf-8", xml_declaration=True)
    atomic_json(work / "raw_strings.json", rows)
    atomic_json(work / "raw_references.json", constants)
    command = [
        str(ROOT / "external/nadeef/mamba_env/bin/java"),
        "-Xmx2g",
        "-cp",
        str(ROOT / "external/llunatic/rich_build")
        + ":"
        + str(ROOT / "external/llunatic/upstream/lib/*"),
        "RichNativeBridge",
        str(path),
        str(work / "native.json"),
        str(work / "raw_strings.json"),
        str(work / "raw_references.json"),
    ]
    with (work / "native.log").open("w") as log:
        subprocess.run(
            command, cwd=work, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=280
        )
    result = read_json(work / "native.json")
    before, unknown = decode(result["before"], rows)
    if before != rows or unknown:
        raise ValueError("Chase input changed exact dirty strings")
    if not result["solutions"]:
        return dict(changes=[], detections=[], unresolved="no native solution")
    after, unresolved = decode(result["after"], rows)
    changes = [
        dict(row=i, col=j, corrected_value=v)
        for i, row in enumerate(after)
        for j, v in enumerate(row)
        if v is not None and v != rows[i][j]
    ]
    return dict(
        changes=changes,
        detections=[dict(row=c["row"], col=c["col"]) for c in changes],
        unresolved=unresolved,
    )


def dc_line(ps, tuples):
    operators = {"eq": "EQ", "ne": "IQ"}
    predicates = []
    for p in ps:
        left = p["left"]["row"] + "." + p["left"]["column"]
        right = (
            '"' + p["right"]["value"] + '"'
            if "value" in p["right"]
            else p["right"]["row"] + "." + p["right"]["column"]
        )
        predicates.append(operators[p["op"]] + "(" + left + "," + right + ")")
    return ("t1&t2&" if tuples == 2 else "t1&") + "&".join(predicates)


def run_native_dc(recipe, rows):
    from baselines.native.external.adr.dc_correction import CLASSES, grid, load_upstream

    method = recipe["method"]
    work = Path(recipe["work"])
    work.mkdir(parents=True, exist_ok=False)
    native = load_upstream(method)
    native.PERFECTED = False
    native.ONLYED = False
    native.dirty_path = native.clean_path = recipe["dirty_csv"]
    lines = []
    for spec in recipe["specs"]:
        if spec["kind"] == "fd":
            fd = spec["fd"]
            lines.append(
                "t1&t2&"
                + "&".join(
                    [f"EQ(t1.{c},t2.{c})" for c in fd["lhs"]]
                    + [f"IQ(t1.{fd['rhs']},t2.{fd['rhs']})"]
                )
            )
        elif spec["kind"] == "pair_line":
            lines.append(spec["line"])
        else:
            lines.extend(dc_line(ps, 1) for ps in spec["clauses"])
    path = work / "constraints.txt"
    path.write_text("\n".join(lines) + ("\n" if method == "holistic" else ""))

    class InferenceComplete(Exception):
        pass

    class Observed(getattr(native, CLASSES[method])):
        def detect(self, maypair, data):
            # Enumeration only: one-row DCs see each physical row once, including
            # the last row. Original predicate evaluation and repair are used.
            for i, rule in enumerate(self.Rules):
                if rule.variable == ["t1"]:
                    maypair[i] = [[[j, j] for j in range(len(data))]]
            return super().detect(maypair, data)

        def repair(self, data, violations):
            self.initial_violations = len(violations)
            super().repair(data, violations)
            raise InferenceComplete()

    cleaner = Observed()
    with contextlib.ExitStack() as stack:
        log = stack.enter_context((work / "native.log").open("w"))
        stack.enter_context(contextlib.redirect_stdout(log))
        stack.enter_context(contextlib.redirect_stderr(log))
        try:
            cleaner.run(str(path), recipe["dirty_csv"], recipe["dirty_csv"])
        except InferenceComplete:
            pass
        else:
            raise ValueError("Native DC repair did not finish")
        finally:
            if method == "bigdansing":
                native.tracemalloc.stop()
    frame = native.pd.read_csv(recipe["dirty_csv"])
    if method == "bigdansing":
        frame = frame.astype(str).fillna("nan")
    for (i, j), v in cleaner.repaired_cells_value.items():
        frame.iloc[i, j] = v
    frame.to_csv(work / "repaired.csv", index=False)
    _, after = grid(work / "repaired.csv")
    if len(after) != len(rows):
        raise ValueError("Native DC repair changed row count")
    proposals = [
        dict(row=i, col=j, corrected_value=after[i][j])
        for i, j in sorted(cleaner.repaired_cells_value)
    ]
    return dict(
        changes=[p for p in proposals if p["corrected_value"] != rows[p["row"]][p["col"]]],
        detections=[dict(row=i, col=j) for i, j in sorted(set(cleaner.repaired_cells))],
        native_proposals=proposals,
    )


def run_old(recipe):
    from baselines.native.constraints.comparison_worker import execute

    fds = [s["fd"] for s in recipe["specs"]]
    return execute(
        dict(
            method=recipe["method"],
            dirty_csv=recipe["dirty_csv"],
            work=recipe["work"],
            rules=fds,
            lines=[],
            seed=recipe["seed"],
            settings=recipe["settings"],
        )
    )


def worker(path):
    recipe = read_json(path)
    started = time.time()
    allowed = {
        "method",
        "dirty_csv",
        "dirty_sha256",
        "specs",
        "seed",
        "settings",
        "work",
        "output",
        "source_sha256",
        "project",
        "recipe_sha256",
    }
    if set(recipe) != allowed:
        raise ValueError(
            "Native worker accepts only dirty data, constraint specifications and runtime settings"
        )
    if (
        fingerprint({k: v for k, v in recipe.items() if k != "recipe_sha256"})
        != recipe["recipe_sha256"]
    ):
        raise ValueError("Rich native recipe seal mismatch")
    if sha(Path(recipe["dirty_csv"])) != recipe["dirty_sha256"] or any(
        sha(Path(p)) != h for p, h in recipe["source_sha256"].items()
    ):
        raise ValueError("Native input/source changed")
    if recipe["method"] in {"bigdansing", "holistic", "horizon", "daisyp"}:
        import resource

        limit = int(recipe["settings"]["light_memory_gib"] * 1024**3)
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    try:
        from baselines.constraints.validation import validate_result
        from baselines.native.external.adr.dc_correction import grid

        header, rows = grid(Path(recipe["dirty_csv"]))
        if not recipe["specs"]:
            result = dict(changes=[], detections=[], abstention="no compatible native constraints")
        elif recipe["method"] == "nadeef":
            result = run_nadeef(recipe, rows)
        elif recipe["method"] == "llunatic":
            result = run_llunatic(recipe, rows)
        elif recipe["method"] == "uniclean":
            from baselines.constraints.rich_uniclean_runtime import execute

            result = execute(recipe, rows)
        elif recipe["method"] in {"bigdansing", "holistic"}:
            result = run_native_dc(recipe, rows)
        else:
            result = run_old(recipe)
        validate_result(result, rows, header, None)
        result["status"] = "complete"
    except Exception as exc:
        result = dict(status="failed", error=type(exc).__name__ + ": " + str(exc), changes=[])
        Path(recipe["output"]).parent.mkdir(parents=True, exist_ok=True)
        Path(recipe["output"]).with_suffix(".failure.txt").write_text(traceback.format_exc())
    result.update(recipe_sha256=recipe["recipe_sha256"], elapsed_seconds=time.time() - started)
    atomic_json(Path(recipe["output"]), result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recipe", type=Path, required=True)
    args = parser.parse_args()
    worker(args.recipe)


if __name__ == "__main__":
    main()
