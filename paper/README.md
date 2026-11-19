# Full paper

- [Read the full paper](CleanBench-full.pdf) (24 pages).
- [Download the Overleaf source package](CleanBench-overleaf.zip).

Recent revisions are highlighted in blue for author review. Set
`\togglefalse{reviewchanges}` in `preamble.tex` for the clean presentation.

This version includes the constraint-selection experiment, violation coverage,
and updated Table 3 results using five-fold semantic-score selection for seven
constraint-based methods. The macro results and associated analyses use the
updated configurations.

Run `python scripts/reproduce_constraints.py` from the repository root to
reproduce the selected constraint results and threshold comparisons.
The existing `scripts/reproduce_table3.py` bundle uses the original constraint
inputs for comparison; it has not yet been replaced with the latest Table 3 cohort.
