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
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable, Optional

import psycopg
from app.judge import judge, to_db_scale
from app.datasets import Case, Dataset, DatasetError, load_dataset
from app.llm_client import MODEL_REGISTRY, LLMResponse, call_llm
from app.prompts.repository import PostgresPromptRepository, PromptRepository
from app.results_repository import (
    ModelNotFoundError,
    ModelRow,
    PostgresResultsRepository,
    ResultsRepository,
)


SYSTEM_PROMPT_NAME = "eval_system"
DEFAULT_MAX_WORKERS = 6
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
    do_judge: bool = False,
    call: Callable[..., LLMResponse] = call_llm,
) -> RunOutcome:
    """Fan out every (case, model) call, persist each result as it returns.

    Returns a `RunOutcome` with insert/failure counts and in-memory
    metrics aggregates (total cost, tokens, latencies). Always calls
    `repo.finalize_run` before returning, even if every task raises.
    """
    inserted = 0
    failed = 0
    total_cost = Decimal(0)
    total_input_tokens = 0
    total_output_tokens = 0
    latencies_ms: list[int] = []

    def task(case: Case, model_key: str, model_row: ModelRow) -> TaskResult:
        provider = MODEL_REGISTRY[model_key].provider
        prompt = build_prompt(system_prompt, case.prompt)
        response = call(provider, model_key, prompt, temperature=temperature)
        return TaskResult(
            case_id=case.id,
            question=case.prompt,
            model_row=model_row,
            response=response,
            prompt_style=case.raw.get("style"),
        )

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures: dict[concurrent.futures.Future, tuple[str, str]] = {}
            for case in dataset.cases:
                for model_key, model_row in model_pairs:
                    fut = ex.submit(task, case, model_key, model_row)
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
                    response=result.response.content,
                    latency_ms=latency_ms,
                    input_tokens=result.response.tokens_in,
                    output_tokens=result.response.tokens_out,
                    cost=cost,
                    prompt_style=result.prompt_style,
                )
                if do_judge:
                    # Judging is best-effort: a judge failure (bad verdict,
                    # API quota, rate-limit, network) must never lose the
                    # response row or kill the run — leave judge_score NULL.
                    try:
                        verdict = judge(result.question, result.response.content)
                        repo.update_judge(
                            result_id=result_id,
                            judge_score=to_db_scale(verdict.score),
                            judge_reasoning=verdict.reasoning,
                        )
                    except Exception as e:
                        print(
                            f"  ⚠ judge failed case={case_id!r} model={model_key!r} "
                            f"→ {type(e).__name__}: {e}",
                            file=sys.stderr,
                        )
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
        help="Sampling temperature (ignored by reasoning models).",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Judge each response with Gemini and persist judge_score/judge_reasoning.",
    )
    return parser.parse_args(argv)


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
            f"cases={len(dataset.cases)}  models={len(model_pairs)}"
        )

        outcome = execute_run(
            dataset=dataset,
            model_pairs=model_pairs,
            system_prompt=system_prompt,
            run_id=run_id,
            repo=results_repo,
            max_workers=args.max_workers,
            temperature=args.temperature,
            do_judge=args.judge,
        )

        total = len(dataset.cases) * len(model_pairs)
        print(f"\n{outcome.inserted}/{total} results inserted, {outcome.failed} failed.")
        if outcome.inserted:
            print(
                f"Summary: total cost=${outcome.total_cost:.6f}  "
                f"tokens={outcome.total_input_tokens}/{outcome.total_output_tokens}  "
                f"latency min/avg/max={outcome.min_latency_ms}/"
                f"{outcome.avg_latency_ms:.0f}/{outcome.max_latency_ms} ms"
            )
        return EXIT_PARTIAL_FAILURE if outcome.failed else EXIT_OK
    finally:
        conn.close()
