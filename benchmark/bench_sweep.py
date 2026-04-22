"""Run a sweep of parser configs against a fixture and print a comparison table.

Every config shares ONE global semaphore so aggregate in-flight requests stay
bounded (Anthropic Max guidance: 3–5 concurrent). Configs overlap for wall-clock
speedup rather than serializing.

Usage:
    uv run python benchmark/bench_sweep.py --fixture 1d
    uv run python benchmark/bench_sweep.py --fixture 1d --config configs/custom.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from tqdm.asyncio import tqdm as atqdm

sys.path.insert(0, str(Path(__file__).parent))
from bench_categorize import (  # noqa: E402
    load_fixture,
    load_labels,
    score_fixture)
from label import run_parser  # noqa: E402

BENCH_DIR = Path(__file__).parent
FIXTURES_DIR = BENCH_DIR / "fixtures"
LABELS_DIR = BENCH_DIR / "labels"
RESULTS_DIR = BENCH_DIR / "results"


async def run_one_config(
    cfg: dict,
    chunks: list[dict],
    sem: asyncio.Semaphore) -> tuple[str, dict[str, list[dict]]]:
    """Run the parser on every chunk for one config; return (name, preds_by_sid)."""
    name = cfg["name"]

    async def one(chunk):
        async with sem:
            try:
                items = await run_parser(chunk, model=cfg["model"], effort=cfg.get("effort", "low"))
                return chunk["sample_id"], items
            except Exception as e:
                print(f"  [{name}/{chunk['sample_id']}] error: {e}")
                return chunk["sample_id"], []

    results = await atqdm.gather(
        *[one(c) for c in chunks],
        desc=f"{name}",
        unit="chunk",
        position=None,  # tqdm picks a row
    )
    return name, dict(results)


def _augment_run_parser_signature():
    """Monkey-patch label.run_parser to accept (chunk, model, effort) kwargs.

    The original signature takes only chunk. We need to override model/effort
    per config for the sweep. This is done inline to avoid editing label.py.
    """
    import label

    async def run_parser_cfg(chunk: dict, model: str = "claude-haiku-4-5", effort: str = "low"):
        from course_scout.infrastructure.agents import (
            AgentOrchestrator,
            StructuredMessage,
            SummarizerInputSchema)
        orch = AgentOrchestrator(summarizer_model=model, effort=effort)
        agent = orch.get_summarizer_agent()
        msgs = [StructuredMessage(**m) for m in chunk["messages"]]
        input_data = SummarizerInputSchema(
            messages=msgs,
            topic_context=chunk["topic_context"],
            chat_message=chunk.get("chat_message", ""))
        result = await agent.run(input_data)
        out = []
        for it in result.items:
            d = it.model_dump() if hasattr(it, "model_dump") else dict(it)
            out.append({
                "title": d.get("title", ""),
                "category": d.get("category", ""),
                "priority": d.get("priority"),
                "status": d.get("status"),
                "description": (d.get("description") or "")[:180],
            })
        return out

    label.run_parser = run_parser_cfg


def render_sweep_table(rows: list[dict]) -> str:
    """Pretty table comparing configs."""
    header = f"{'config':<20s}  {'P':>6s}  {'R':>6s}  {'F1':>6s}  {'cat_acc':>8s}  {'tp':>4s}  {'fp':>4s}  {'fn':>4s}"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in rows:
        lines.append(
            f"{r['name']:<20s}  "
            f"{r['precision']:>6.3f}  {r['recall']:>6.3f}  {r['f1']:>6.3f}  "
            f"{r['cat_acc'] if r['cat_acc'] is not None else 0:>8.3f}  "
            f"{r['tp']:>4d}  {r['fp']:>4d}  {r['fn']:>4d}"
        )
    return "\n".join(lines)


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", required=True)
    ap.add_argument("--config", default="configs/default.yaml")
    args = ap.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.is_absolute():
        cfg_path = BENCH_DIR / args.config
    sweep = yaml.safe_load(cfg_path.read_text())

    global_conc = int(sweep.get("global_concurrency", 5))
    bench_kind = sweep.get("bench", "categorize")
    if bench_kind != "categorize":
        raise SystemExit(f"Only bench=categorize is wired into sweep for now (got: {bench_kind})")

    fixture = load_fixture(FIXTURES_DIR / f"{args.fixture}.jsonl")
    labels = load_labels(LABELS_DIR / f"{args.fixture}.yaml")
    labeled_sids = set(labels["samples"].keys())
    to_run = [c for c in fixture if c["sample_id"] in labeled_sids]

    if not to_run:
        raise SystemExit(f"No labeled samples for fixture {args.fixture}. Run label.py first.")

    # run_parser now accepts model + effort directly — no monkey-patch needed
    sem = asyncio.Semaphore(global_conc)

    print(
        f"Sweep: {len(sweep['configs'])} configs × {len(to_run)} samples "
        f"(global concurrency={global_conc}) on fixture {args.fixture}\n"
    )

    # Launch all configs in parallel; they share the semaphore
    tasks = [run_one_config(cfg, to_run, sem) for cfg in sweep["configs"]]
    config_results = await asyncio.gather(*tasks)

    # Score each config
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    rows = []
    for name, preds_by_sid in config_results:
        report = score_fixture(fixture, labels, preds_by_sid)
        cfg = next(c for c in sweep["configs"] if c["name"] == name)
        report["meta"] = {
            "fixture": args.fixture,
            "model": cfg["model"],
            "effort": cfg.get("effort", "low"),
            "name": name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        out = RESULTS_DIR / f"sweep_{args.fixture}_{name}_{ts}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2))

        s = report["set_scores"]
        rows.append({
            "name": name,
            "precision": s["precision"],
            "recall": s["recall"],
            "f1": s["f1"],
            "tp": s["tp"],
            "fp": s["fp"],
            "fn": s["fn"],
            "cat_acc": report["field_accuracy_on_matched"].get("category"),
        })

    rows.sort(key=lambda r: -r["f1"])
    print("\n" + render_sweep_table(rows))

    # Also write a combined summary
    summary_path = RESULTS_DIR / f"sweep_{args.fixture}_{ts}_summary.json"
    summary_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    print(f"\nSummary: {summary_path}")


if __name__ == "__main__":
    asyncio.run(main())
