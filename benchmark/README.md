# course-scout benchmarks

Two independent benches for the two LLM stages in the pipeline:

```
Stage 1: CATEGORIZE (LLM)  → {category, status}   ← bench_categorize.py
Stage 2: ROUTE (python)    → priority              ← not benched (deterministic)
Stage 3: PREFERENCE (LLM)  → top-5 ranking         ← bench_preference.py
```

## Layout

```
benchmark/
├── sample.py                    # fixtures/{N}d.jsonl from logs
├── label.py                     # interactive labeler for CATEGORIZE
├── autolabel_categorize.py      # run parser once, self-label (smoke test)
├── bench_categorize.py          # score category accuracy
├── bench_preference.py          # score Precision@5 on top-5 ranking
├── quick.py                     # one-shot autolabel + categorize eval
├── fixtures/{1d,7d,30d}.jsonl   # parser-input chunks
├── labels/
│   ├── {1d,7d,30d}.yaml         # category labels (per chunk)
│   └── preference_{1d,7d,30d}.yaml  # relevance tags (per pool item)
└── results/                     # cached pools, predictions, score reports
```

## Categorize bench

Scores the parser's `category` field only. Priority and status are either
deterministic (priority) or downstream (status).

```bash
# Label categories (resumable)
uv run python benchmark/label.py --fixture 1d

# Score
uv run python benchmark/bench_categorize.py --fixture 1d
uv run python benchmark/bench_categorize.py --fixture 1d --model claude-sonnet-4-6
```

Reports set P/R/F1, category accuracy on matched items, per-class P/R/F1, and
a category confusion matrix.

## Preference bench

Scores stage-3 top-5 ranking against Adam's relevance labels.

```bash
# 1. Build item pool + interactively tag each as R / M / I
uv run python benchmark/bench_preference.py --fixture 1d --label

# 2. Score
uv run python benchmark/bench_preference.py --fixture 1d
```

Reports **Precision@5** (fraction of picks tagged RELEVANT), plus a breakdown
of RELEVANT / MAYBE / IRRELEVANT / UNKNOWN per pick.

## Quick iteration

```bash
uv run python benchmark/quick.py               # categorize bench on 1d
uv run python benchmark/quick.py --model claude-sonnet-4-6
```
