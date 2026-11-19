# Full paper

- [Read the full paper](CleanBench-full.pdf) (24 pages).
- [Download the Overleaf source package](CleanBench-overleaf.zip).

Recent revisions are highlighted in blue for author review. Set
`\togglefalse{reviewchanges}` in `preamble.tex` for the clean presentation.

Table 3 uses the original method configurations and evaluates all 1,409 labeled
error cells. The constraint-generation, score-threshold, and violation-coverage
experiments are reported separately in the appendix.

From the repository root, run `python scripts/reproduce_table3.py` to reproduce
Table 3 from its 59 saved runs, and `python scripts/reproduce_constraints.py` to
reproduce the appendix's threshold and selection results.
