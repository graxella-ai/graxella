"""LongMemEval benchmark harness for Mnema.

LongMemEval (Wu et al. 2024) — 500 questions testing long-term memory
over multi-session chat histories.

Dataset format (each top-level item is ONE question):
  {
    "question_id": str,
    "question_type": str,
    "question": str,
    "answer": str,
    "haystack_sessions": [  # list of sessions
      [{"role": "user/assistant", "content": "...", "has_answer": bool}, ...]
    ]
  }

Pipeline per question:
  1. Feed all haystack session turns as Mnema observations
  2. Sleep-phase consolidation (LLM extracts rules/skills)
  3. Semantic search to retrieve top-k beliefs for this question
  4. LLM answers from the retrieved context
  5. Exact-match vs gold answer

Download dataset:
  cd LongMemEval/data/
  python -c "
  import urllib.request
  for f in ['longmemeval_oracle.json', 'longmemeval_s_cleaned.json']:
      urllib.request.urlretrieve(
          f'https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/{f}',
          f)
  "

Usage:
  python benchmarks/run_longmemeval.py \\
      --dataset ../LongMemEval/data/longmemeval_oracle.json \\
      --model qwen2.5:7b [--max-items 50]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from mnema.adapters.sqlite.repository import SqliteMnemaStore
from mnema.services.consolidator import SleepConsolidator
from mnema.services.recorder import MemoryRecorder
from mnema.services.retriever import MemoryRetriever


def _load_dataset(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else []


def _contains(pred: str, gold: str) -> bool:
    """Soft match: gold answer appears in the prediction (case-insensitive)."""
    return gold.strip().lower() in pred.strip().lower()


def _exact_match(pred: str, gold: str) -> bool:
    return pred.strip().lower() == gold.strip().lower()


def run_benchmark(
    dataset_path: str,
    *,
    use_fake: bool = False,
    ollama_model: str = "qwen2.5:7b",
    max_items: int | None = None,
    score_mode: str = "contains",   # "exact" or "contains"
) -> dict:
    if use_fake:
        from mnema.adapters.llm.fake import FakeLLM
        llm = FakeLLM.from_scenario("weatherlib_v2")
    else:
        from mnema.adapters.llm.ollama import OllamaLLM
        llm = OllamaLLM(model=ollama_model, timeout=300.0)  # 5 min for 7B inference

    items = _load_dataset(dataset_path)
    if max_items:
        items = items[:max_items]

    total = 0
    correct = 0
    latencies: list[float] = []
    misses: list[dict] = []

    llm_label = "FakeLLM" if use_fake else ollama_model
    print("LongMemEval — Mnema benchmark")
    print(f"Dataset   : {Path(dataset_path).name}")
    print(f"Items     : {len(items)}")
    print(f"LLM       : {llm_label}")
    print(f"Scoring   : {score_mode}")
    print()

    score_fn = _contains if score_mode == "contains" else _exact_match

    for i, item in enumerate(items):
        qid = item.get("question_id", f"q{i}")
        question = item.get("question", "")
        gold = str(item.get("answer", ""))
        haystack_sessions: list[list[dict]] = item.get("haystack_sessions", [])

        # Fresh in-memory store per question item
        store = SqliteMnemaStore("sqlite:///:memory:")
        rec = MemoryRecorder(store)
        agent_id = qid

        # Feed all session turns as observations
        for session_idx, session in enumerate(haystack_sessions):
            subject = f"session_{session_idx}"
            for turn in session:
                role = turn.get("role", "")
                content = turn.get("content", "")
                if role in ("user", "assistant") and content:
                    rec.observe(agent_id, f"[{role}] {content}", subject=subject)

        # Sleep-phase consolidation extracts rules + detects contradictions
        # Failure is non-fatal — raw observations remain available for retrieval
        import contextlib
        with contextlib.suppress(Exception):
            SleepConsolidator(store, llm).consolidate(agent_id)

        retriever = MemoryRetriever(store)

        t0 = time.perf_counter()

        # Semantic search ranks beliefs by query relevance
        ranked = retriever.semantic_search(agent_id, question, top_k=5)
        if ranked:
            context = "\n".join(f"- {b.statement}" for _, b in ranked)
        else:
            context = retriever.render(agent_id)

        pred = llm.answer_question(context, question)
        latency_ms = (time.perf_counter() - t0) * 1000
        latencies.append(latency_ms)

        hit = score_fn(pred, gold)
        correct += hit
        total += 1

        if not hit:
            misses.append({"qid": qid, "q": question, "gold": gold, "pred": pred})

        if (i + 1) % 25 == 0:
            print(
                f"  [{i+1:4d}/{len(items)}]  "
                f"acc={correct/total*100:.1f}%  "
                f"avg_lat={sum(latencies)/len(latencies):.0f}ms"
            )

    accuracy = correct / total * 100 if total > 0 else 0.0
    avg_lat = sum(latencies) / len(latencies) if latencies else 0.0
    p95_lat = sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0.0

    results = {
        "benchmark": "LongMemEval",
        "dataset": Path(dataset_path).name,
        "items": len(items),
        "total_questions": total,
        "correct": correct,
        "accuracy_pct": round(accuracy, 2),
        "avg_latency_ms": round(avg_lat, 2),
        "p95_latency_ms": round(p95_lat, 2),
        "llm": llm_label,
        "score_mode": score_mode,
    }

    _print_results(results, misses[:5])
    return results


def _print_results(r: dict, sample_misses: list[dict]) -> None:
    print()
    print("=" * 65)
    print("  LongMemEval Results — Mnema")
    print("=" * 65)
    print(f"  Dataset            : {r['dataset']}")
    print(f"  Items evaluated    : {r['items']}")
    print(f"  Scoring mode       : {r['score_mode']}")
    print(f"  LLM                : {r['llm']}")
    print()
    print(f"  Correct            : {r['correct']} / {r['total_questions']}")
    print(f"  Accuracy           : {r['accuracy_pct']:.1f}%")
    print(f"  Avg latency        : {r['avg_latency_ms']:.1f} ms")
    print(f"  P95 latency        : {r['p95_latency_ms']:.1f} ms")
    print()
    print("  Published comparison (GPT-4-turbo, full haystack):")
    print("    Zep   : 94.8%   MemGPT : 93.4%   ReadAgent : 82.4%")
    print(f"  Mnema   : {r['accuracy_pct']:.1f}%  ({r['llm']}, oracle haystack)")
    if sample_misses:
        print()
        print("  Sample misses:")
        for m in sample_misses:
            print(f"    Q: {m['q'][:70]}")
            print(f"    Gold: {m['gold']}  |  Pred: {m['pred'][:60]}")
            print()
    print("=" * 65)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LongMemEval benchmark for Mnema")
    parser.add_argument("--dataset", required=True, help="Path to LongMemEval JSON file")
    parser.add_argument("--fake", action="store_true", help="Use FakeLLM (for CI/testing)")
    parser.add_argument("--model", default="qwen2.5:7b", help="Ollama model tag")
    parser.add_argument("--max-items", type=int, default=None, help="Limit items (quick run)")
    parser.add_argument(
        "--score", default="contains", choices=["exact", "contains"],
        help="Scoring mode: 'contains' (gold in pred) or 'exact'"
    )
    args = parser.parse_args()

    if not os.path.exists(args.dataset):
        print(f"ERROR: Dataset not found: {args.dataset}")
        print("Download: see module docstring for wget commands")
        sys.exit(1)

    run_benchmark(
        args.dataset,
        use_fake=args.fake,
        ollama_model=args.model,
        max_items=args.max_items,
        score_mode=args.score,
    )
