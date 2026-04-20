# Benchmark TODOs

## Ground-truth labeling (required for real F1 signal)

Current benches measure parser self-consistency (Adam hasn't hand-labeled yet).
All P/R/F1 numbers are lower bounds on the true error rate — a systematic
error pattern would still show ~0.85 F1 against self-labels.

**Plan when Adam has energy**:
1. `uv run python benchmark/label.py --fixture 1d` — ~20 min for 13 chunks
2. `uv run python benchmark/label.py --fixture 7d` — ~2 hrs for 78 chunks (canonical)
3. Rerun `bench_categorize.py` and compare to self-label baseline:
   - Self-label F1 ≈ 0.85 (measured)
   - Real F1 = ?
   - Gap = systematic parser errors hidden by self-consistency

## Verify coalesced discussion consolidation

The coalesced run produced -10 `discussion` items vs chunked (-19.6%). Hypothesis:
same thread was being split across chunk boundaries, creating duplicate items in
chunked mode. Need to spot-check 2-3 coalesced discussion items against their
chunked counterparts to confirm this is consolidation, not loss.

## Investigate schema validation errors on large outputs

3 chunks in coalesced 7d run failed with Pydantic `Extra data` error:
  - s0017 (twice), s0046
JSON output from the model has trailing garbage after valid items array.
Might need to: (a) add json-repair tolerance, (b) cap output size, or (c) use
streaming output parse. Only surfaces on coalesced-mode large inputs.

## Token-overflow fallback

Wire `pick_model(tokens)` into `run_parser`:
  - <= 180K → claude-haiku-4-5
  - <= 950K → claude-sonnet-4-6 (1M native)
  - else → claude-opus-4-7 (1M native)
Not urgent — current coalesced fixtures all fit Haiku's 200K.

## Preference bench ground-truth

`bench_preference.py` needs Adam to tag pool items as RELEVANT/MAYBE/IRRELEVANT.
Same deferral as categorize labeling.

---

## Practitioner-recommended improvements (from researcher Apr 2026)

Sources: Hamel Husain (LLM Evals FAQ), Eugene Yan (Product Evals), Shreya Shankar (SPADE).

### Confirmed alignments with current setup
- 50-100 sample fixture size matches the recommended solo-dev scale (Husain says
  20-50 is enough). Don't aim higher.
- Per-channel slicing (already in `group_by.py`) is the right shape — they call
  this "slice-based evals". Aggregate F1 hides regressions in rare classes.

### Recommended additions

1. **Binary pass/fail per item** (in addition to Hungarian F1).
   Husain: *"Binary evaluations force clearer thinking and more consistent labeling.
   The difference between Likert 3 vs 4 is subjective."*
   Concretely: for each labeled item, score each field (category, status) as
   right/wrong. Report per-slice pass-rate alongside set F1.

2. **LLM-as-judge with alignment** to scale labeling.
   Pattern: hand-label ~20 items, then have a stronger model (sonnet/opus) judge
   the next batch. Measure judge↔Adam agreement (Cohen's κ or raw %). Iterate
   on judge prompt until ≥0.8 agreement, then let it label at scale.

3. **Hard-set + easy-set split**.
   Evidently: *"include tough cases your current LLM doesn't get right.
   These give you a baseline for improvement."*
   If every run scores 100% on the bench, the bench is dead.

4. **Version fixtures alongside prompts**.
   Schema/rubric WILL drift as you see new outputs (SPADE paper proves this is
   structural, not a bug). Git-tag fixture versions when rubric changes; keep
   old versions runnable for regression archaeology.

5. **Add to fixture monotonically**.
   Every production failure → add that trace + label to fixture. Never replace,
   only append (until rubric break forces a new version).

### Confirmed anti-patterns we should avoid

- **Self-consistency labels = label leakage**. Our current 0.878 F1 is parser-
  vs-parser, which Husain explicitly warns against. Real ground truth required
  for any meaningful number.
- **Generic metrics (BERTScore, ROUGE)** as primary — not useful for our task.
- **Unvalidated LLM-as-judge** — must align against human labels first.

### Canonical reading order (when energy permits)

1. https://hamel.dev/blog/posts/evals-faq/  (FAQ — quickest)
2. https://eugeneyan.com/writing/product-evals/  (3-step playbook)
3. https://hamel.dev/blog/posts/llm-judge/  (LLM-as-judge deep dive)
4. https://arxiv.org/abs/2404.12272  (SPADE — academic, optional)
