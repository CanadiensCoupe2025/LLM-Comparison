"""Evaluation runner (SCRUM-19).

Given a YAML dataset and a list of model keys, fan calls out across all
(case, model) pairs in parallel via a ThreadPoolExecutor, then persist each
result to PostgreSQL — synchronously, on the main thread, as futures resolve.

CLI :
    python runner.py --dataset evaluator/datasets/regression_v1.yaml \\
                     --models claude-sonnet-4-6 deepseek-v4-flash \\
                     --max-workers 6

Exit codes:
    0  every call succeeded and was persisted
    1  configuration error (env var missing, DB unreachable, system prompt absent)
    2  bad CLI arguments (handled by argparse → exits 2 itself)
    3  the run completed but at least one model call raised
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import statistics
import sys
import time
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

import psycopg
from app.judge import judge, to_db_scale
from app.datasets import Case, Dataset, DatasetError, load_dataset
from app.llm_client import MODEL_REGISTRY, LLMResponse, call_llm
from app.style_features import StyleFeatures, extract_style_features
from app.prompts.repository import PostgresPromptRepository, PromptRepository
from app.results_repository import (
    ModelNotFoundError,
    ModelRow,
    PostgresResultsRepository,
    ResultsRepository,
)


SYSTEM_PROMPT_NAME = "eval_system"
DEFAULT_MAX_WORKERS = 6
# High by default: tokens are budgeted and a single draw is a noisy point
# estimate. N=1 reproduces the old single-shot behaviour for quick smoke runs.
DEFAULT_SAMPLES = 10
EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_PARTIAL_FAILURE = 3


# ---------------------------------------------------------------------------
# Pure helpers (no DB, no IO) — easy to unit-test in isolation.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskResult:
    """What a single (case, model) thread returns. Cost is computed by the
    main thread after futures resolve so the workers stay pure."""

    case_id: str
    question: str
    model_row: ModelRow
    response: LLMResponse
    prompt_style: Optional[str] = None
    sample_idx: int = 0
    style: Optional[StyleFeatures] = None


@dataclass
class RunOutcome:
    """In-memory aggregation of a run's results (SCRUM-22).

    Persisted metrics still live in `results` (per row); this is the
    summary the runner prints to stdout after a run completes. Grafana
    and ad-hoc analysis should query the `run_metrics` view (migration
    004) for the same shape over historical data.
    """

    inserted: int
    failed: int
    total_cost: Decimal
    total_input_tokens: int
    total_output_tokens: int
    latencies_ms: list[int]
    # SCRUM-37: scaled (0–5) judge scores bucketed by (model_key, prompt_style),
    # populated only on judged benchmark runs. Empty otherwise.
    style_scores: dict[tuple[str, str], list[Decimal]] = field(default_factory=dict)
    # Repeated sampling: every scaled judge score per model_key, so the run
    # summary can report mean ± spread. Populated only on judged runs.
    model_scores: dict[str, list[Decimal]] = field(default_factory=dict)

    def model_score_stats(self) -> dict[str, tuple[float, float, int]]:
        """Per-model (mean, sample stddev, n) of judge scores (0–5).

        stddev is 0.0 when a model has fewer than 2 samples — one point has
        no spread. Skips models with no judged samples.
        """
        out: dict[str, tuple[float, float, int]] = {}
        for model_key, scores in self.model_scores.items():
            if not scores:
                continue
            floats = [float(s) for s in scores]
            mean = sum(floats) / len(floats)
            stddev = statistics.stdev(floats) if len(floats) > 1 else 0.0
            out[model_key] = (mean, stddev, len(floats))
        return out

    def style_averages(self) -> dict[tuple[str, str], float]:
        """Mean judge score per (model, style). Skips empty buckets."""
        return {
            key: float(sum(scores) / len(scores))
            for key, scores in self.style_scores.items()
            if scores
        }

    @property
    def avg_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0.0

    @property
    def min_latency_ms(self) -> int:
        return min(self.latencies_ms) if self.latencies_ms else 0

    @property
    def max_latency_ms(self) -> int:
        return max(self.latencies_ms) if self.latencies_ms else 0


def compute_cost(
    *, input_tokens: int, output_tokens: int, model: ModelRow
) -> Decimal:
    """Total USD cost for a single call given the model's per-token prices."""
    return (
        Decimal(input_tokens) * model.input_cost
        + Decimal(output_tokens) * model.output_cost
    )


def build_prompt(system: str, user: str) -> str:
    """Inline-prepend the system prompt to the user message (SCRUM-17's
    `call_llm` doesn't expose a system parameter, so we concatenate)."""
    return f"{system.strip()}\n\n{user}"


def resolve_models(
    keys: list[str], repo: ResultsRepository
) -> list[tuple[str, ModelRow]]:
    """Validate every model key against MODEL_REGISTRY and the DB.

    Returns a list of `(model_key, ModelRow)` pairs, preserving input order.
    Raises ValueError on unknown registry key, ModelNotFoundError on missing
    DB row.
    """
    out: list[tuple[str, ModelRow]] = []
    for key in keys:
        if key not in MODEL_REGISTRY:
            raise ValueError(
                f"Unknown model key {key!r}. "
                f"Known keys: {sorted(MODEL_REGISTRY)}"
            )
        out.append((key, repo.lookup_model(key)))
    return out


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def execute_run(
    *,
    dataset: Dataset,
    model_pairs: list[tuple[str, ModelRow]],
    system_prompt: str,
    run_id: int,
    repo: ResultsRepository,
    max_workers: int,
    temperature: float,
    samples: int = 1,
    do_judge: bool = False,
    judge_min_interval: float = 0.0,
    call: Callable[..., LLMResponse] = call_llm,
) -> RunOutcome:
    """Fan out every (case, model) call, persist each result as it returns.

    Each (case, model) pair is evaluated `samples` times (default 1); every
    draw is persisted as its own `results` row, tagged with `sample_idx`.
    Returns a `RunOutcome` with insert/failure counts and in-memory metrics
    aggregates (total cost, tokens, latencies). Always calls
    `repo.finalize_run` before returning, even if every task raises.
    """
    inserted = 0
    failed = 0
    total_cost = Decimal(0)
    total_input_tokens = 0
    total_output_tokens = 0
    latencies_ms: list[int] = []
    style_scores: dict[tuple[str, str], list[Decimal]] = {}
    model_scores: dict[str, list[Decimal]] = {}
    # Rate-limit throttle: timestamp of the last judge call (monotonic clock).
    last_judge_at: Optional[float] = None

    def task(
        case: Case, model_key: str, model_row: ModelRow, sample_idx: int
    ) -> TaskResult:
        provider = MODEL_REGISTRY[model_key].provider
        prompt = build_prompt(system_prompt, case.prompt)
        response = call(provider, model_key, prompt, temperature=temperature)
        return TaskResult(
            case_id=case.id,
            question=case.prompt,
            model_row=model_row,
            response=response,
            prompt_style=case.raw.get("style"),
            sample_idx=sample_idx,
            # Cheap, pure — compute on the worker thread alongside the response.
            style=extract_style_features(response.content),
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures: dict[concurrent.futures.Future, tuple[str, str]] = {}
            for case in dataset.cases:
                for model_key, model_row in model_pairs:
                    for sample_idx in range(samples):
                        fut = ex.submit(task, case, model_key, model_row, sample_idx)
                        futures[fut] = (case.id, model_key)

            for fut in concurrent.futures.as_completed(futures):
                case_id, model_key = futures[fut]
                try:
                    result = fut.result()
                except Exception as e:
                    failed += 1
                    print(
                        f"  ✗ case={case_id!r} model={model_key!r} → {type(e).__name__}: {e}",
                        file=sys.stderr,
                    )
                    continue

                cost = compute_cost(
                    input_tokens=result.response.tokens_in,
                    output_tokens=result.response.tokens_out,
                    model=result.model_row,
                )
                latency_ms = int(result.response.latency_ms)
                result_id = repo.insert_result(
                    run_id=run_id,
                    model_id=result.model_row.id,
                    case_id=result.case_id,
                    question=result.question,
                    response=result.response.content,
                    latency_ms=latency_ms,
                    input_tokens=result.response.tokens_in,
                    output_tokens=result.response.tokens_out,
                    cost=cost,
                    prompt_style=result.prompt_style,
                    sample_idx=result.sample_idx,
                    resp_style_headers=result.style.headers if result.style else None,
                    resp_style_bold=result.style.bold if result.style else None,
                    resp_style_ordered=result.style.ordered if result.style else None,
                    resp_style_unordered=result.style.unordered if result.style else None,
                    resp_style_code_blocks=(
                        result.style.code_blocks if result.style else None
                    ),
                )
                if do_judge:
                    # Optional pacing between judge calls. Off by default —
                    # judge() already retries 429/503 with exponential backoff,
                    # so this is only needed to cap burst RPM when judging is
                    # parallelized at high --samples.
                    if judge_min_interval > 0 and last_judge_at is not None:
                        wait = judge_min_interval - (time.monotonic() - last_judge_at)
                        if wait > 0:
                            time.sleep(wait)
                    # Judging is best-effort: a judge failure (bad verdict,
                    # API quota, rate-limit, network) must never lose the
                    # response row or kill the run — leave judge_score NULL.
                    try:
                        verdict = judge(result.question, result.response.content)
                        scaled = to_db_scale(verdict.score)
                        repo.update_judge(
                            result_id=result_id,
                            judge_score=scaled,
                            judge_reasoning=verdict.reasoning,
                        )
                        model_scores.setdefault(model_key, []).append(scaled)
                        if result.prompt_style:
                            style_scores.setdefault(
                                (model_key, result.prompt_style), []
                            ).append(scaled)
                    except Exception as e:
                        print(
                            f"  ⚠ judge failed case={case_id!r} model={model_key!r} "
                            f"→ {type(e).__name__}: {e}",
                            file=sys.stderr,
                        )
                    last_judge_at = time.monotonic()
                inserted += 1
                total_cost += cost
                total_input_tokens += result.response.tokens_in
                total_output_tokens += result.response.tokens_out
                latencies_ms.append(latency_ms)
                print(
                    f"  ✓ case={case_id!r} model={model_key!r} "
                    f"tokens={result.response.tokens_in}/{result.response.tokens_out} "
                    f"cost=${cost:.6f}"
                )
    finally:
        repo.finalize_run(run_id)

    return RunOutcome(
        inserted=inserted,
        failed=failed,
        total_cost=total_cost,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        latencies_ms=latencies_ms,
        style_scores=style_scores,
        model_scores=model_scores,
    )


# ---------------------------------------------------------------------------
# CLI plumbing
# ---------------------------------------------------------------------------


def _connect_db():
    """Open a psycopg connection from DATABASE_URL. Exits with code 1 if absent."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set — check your .env or compose env_file.")
    return psycopg.connect(url)


def _load_system_prompt(prompt_repo: PromptRepository) -> tuple[int, str]:
    row = prompt_repo.latest_by_name(SYSTEM_PROMPT_NAME)
    if row is None:
        sys.exit(
            f"System prompt {SYSTEM_PROMPT_NAME!r} not found in the `prompts` table. "
            "Run `python -m app.prompts.cli sync` first."
        )
    return row.id, row.content


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="runner",
        description="Evaluation runner: fan a dataset across N models in parallel.",
    )
    parser.add_argument(
        "--dataset",
        required=True,
        type=Path,
        help="Path to a YAML dataset file.",
    )
    parser.add_argument(
        "--models",
        required=True,
        nargs="+",
        metavar="KEY",
        help="One or more model keys from MODEL_REGISTRY.",
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=DEFAULT_MAX_WORKERS,
        help=f"ThreadPoolExecutor concurrency cap (default: {DEFAULT_MAX_WORKERS}).",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="Sampling temperature (ignored by reasoning models). Use > 0 "
        "(e.g. 0.7) with --samples to get real run-to-run variance.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=DEFAULT_SAMPLES,
        metavar="N",
        help=f"Evaluate each (case, model) pair N times so scores carry a "
        f"mean ± spread instead of one noisy draw (default: {DEFAULT_SAMPLES}). "
        f"N=1 reproduces single-shot behaviour. Needs --temperature > 0 to "
        f"show variance for non-reasoning models.",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Judge each response with Gemini and persist judge_score/judge_reasoning.",
    )
    parser.add_argument(
        "--judge-min-interval",
        type=float,
        default=0.0,
        metavar="SECONDS",
        help="Minimum seconds between judge calls (rate-limit throttle). "
        "0 = off (default); judge() already retries 429s with backoff. Raise "
        "only to cap burst RPM when judging is parallelized at high --samples.",
    )
    return parser.parse_args(argv)


def _print_style_summary(outcome: RunOutcome) -> None:
    """Print the per-(model, style) average judge score (SCRUM-37).

    No-op when the run wasn't a judged benchmark (no style buckets).
    """
    averages = outcome.style_averages()
    if not averages:
        return
    print("\nAvg judge score by prompt style (0–5):")
    by_model: dict[str, list[tuple[str, float]]] = {}
    for (model_key, style), avg in sorted(averages.items()):
        by_model.setdefault(model_key, []).append((style, avg))
    for model_key in sorted(by_model):
        parts = "  ".join(
            f"{style}={avg:.1f} (n={len(outcome.style_scores[(model_key, style)])})"
            for style, avg in by_model[model_key]
        )
        print(f"  {model_key}: {parts}")


def _print_variance_summary(outcome: RunOutcome) -> None:
    """Print per-model mean ± stddev of judge scores across all samples.

    No-op on unjudged runs (no scores collected). With --samples 1 the stddev
    is 0 — the point of high --samples is to make this spread meaningful.
    """
    stats = outcome.model_score_stats()
    if not stats:
        return
    print("\nJudge score mean ± stddev by model (0–5):")
    for model_key in sorted(stats):
        mean, stddev, n = stats[model_key]
        print(f"  {model_key}: {mean:.2f} ± {stddev:.2f} (n={n})")


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv)

    try:
        dataset = load_dataset(args.dataset)
    except DatasetError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_CONFIG

    # Validate model keys upfront — fail before opening the DB.
    unknown = [k for k in args.models if k not in MODEL_REGISTRY]
    if unknown:
        print(
            f"error: unknown model key(s): {unknown}. "
            f"Known: {sorted(MODEL_REGISTRY)}",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    conn = _connect_db()
    try:
        results_repo = PostgresResultsRepository(conn)
        prompt_repo = PostgresPromptRepository(conn)

        try:
            model_pairs = resolve_models(args.models, results_repo)
        except (ValueError, ModelNotFoundError) as e:
            print(f"error: {e}", file=sys.stderr)
            return EXIT_CONFIG

        prompt_id, system_prompt = _load_system_prompt(prompt_repo)

        run_id = results_repo.create_run(prompt_id, args.dataset.name)
        print(
            f"Run id={run_id}  dataset={dataset.name} v{dataset.version}  "
            f"cases={len(dataset.cases)}  models={len(model_pairs)}  "
            f"samples={args.samples}"
        )

        outcome = execute_run(
            dataset=dataset,
            model_pairs=model_pairs,
            system_prompt=system_prompt,
            run_id=run_id,
            repo=results_repo,
            max_workers=args.max_workers,
            temperature=args.temperature,
            samples=args.samples,
            do_judge=args.judge,
            judge_min_interval=args.judge_min_interval,
        )

        total = len(dataset.cases) * len(model_pairs) * args.samples
        print(f"\n{outcome.inserted}/{total} results inserted, {outcome.failed} failed.")
        if outcome.inserted:
            print(
                f"Summary: total cost=${outcome.total_cost:.6f}  "
                f"tokens={outcome.total_input_tokens}/{outcome.total_output_tokens}  "
                f"latency min/avg/max={outcome.min_latency_ms}/"
                f"{outcome.avg_latency_ms:.0f}/{outcome.max_latency_ms} ms"
            )
        _print_variance_summary(outcome)
        _print_style_summary(outcome)
        return EXIT_PARTIAL_FAILURE if outcome.failed else EXIT_OK
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
