# Selecting constraints for constraint-based cleaning

This experiment studies how LLM-assisted generation and selection can provide
suitable constraints for existing cleaning methods. It covers BigDansing,
Holistic, DaisyP, Horizon, NADEEF, LLUNATIC, and UniClean.

GPT-5.2 generated candidates with access to dirty tables, annotated error
locations, and accepted repairs. Candidates were executed on the original dirty
tables and filtered for overlap with annotated errors. Deduplication and
coverage-preserving compression produced a shared bank of 1,912 constraints
across 168 tables. A separate GPT-5.2 pass scored semantic validity from 0 to 100
using dirty tables, rule definitions, and their violations. The fixed cutoffs are
0, 20, 40, 60, 80, and 90. Each method uses its supported native projections.

All 1,000 tables are evaluated with the same scoring rule as Table 3. Ordinary errors earn credit for an accepted repair. For each bidirectional consistency component, credit equals the positive reduction in the number of edits needed to reach a valid completion. The recall denominator is 1,178 (1,078 ordinary errors and 100 units of initial repair distance).
Five-fold selection groups source workbooks and duplicate tables, chooses a
method's cutoff on the other four folds, and aggregates evaluation counts.
The GT-assisted candidate bank is fixed across all folds. Three score-shuffling
controls preserve native input counts at each cutoff. Each distinct method/input
combination has one saved execution.

## Reproduce the reported results

Python 3.10+ is sufficient; no API key or third-party cleaning installation is
needed for this command:

```bash
python scripts/reproduce_constraints.py
```

It verifies checksums, scores the 147 saved method/input runs, reconstructs all
42 threshold points and the five-fold comparisons, and prints `MATCH` when the
results agree with the full-paper appendix. Output CSVs are written under
`build/constraints/`.

## Run the cleaning methods again

Docker images download upstream implementations at the commits pinned in
`docker/setup_upstream.py`. Project-authored native adapters are under
`baselines/constraints/` and `baselines/native/`.

Build only the images needed for the selected methods:

```bash
python scripts/run_constraint_sweep.py build --method nadeef
python scripts/run_constraint_sweep.py build --method uniclean
```

Run all six cutoffs for one method:

```bash
python scripts/run_constraint_sweep.py run --method nadeef \
  --output outputs/nadeef_scores
```

Run all seven methods and all three randomized controls:

```bash
python scripts/run_constraint_sweep.py build
python scripts/run_constraint_sweep.py run --controls \
  --output outputs/constraint_scores
```

Use `--method` repeatedly to select methods, `--threshold` repeatedly to select
cutoffs, and `--workers` to control per-method table concurrency. Add `--dry-run`
to print the Docker commands. Each invocation uses a new output directory.
The six-cutoff runs also produce five-fold threshold selections. The shuffled
controls reuse a native output only when its method and input bank are identical.
Fresh executions do not read saved predictions.

The supplied bank and scores are sufficient to rerun these methods without an
API key. Generation and review prompts are saved under
`baselines/constraints/prompts/`; the reported experiment uses the fixed bank.

## Files

| Location | Contents |
| --- | --- |
| `baselines/constraints/config/pool.json` | Rule definitions, semantic scores, native projections |
| `baselines/constraints/config/selections.json` | Numeric and randomized bank memberships |
| `baselines/constraints/config/runtime.json` | Seeds and native time limits |
| `reproducibility/constraints/reviews.json` | Model scores and their explanations |
| `reproducibility/constraints/runs/` | Saved predictions and complete execution maps |
| `reproducibility/constraints/expected/` | Full-paper threshold and comparison CSVs |
| `reproducibility/constraints/manifest.json` | Checksums and native input fingerprints |

Upstream implementations are fetched during image builds; their repositories
and licenses remain authoritative. The wrappers and source pins are included in
this repository. No API keys, browser profiles, or local authentication files are
included.
