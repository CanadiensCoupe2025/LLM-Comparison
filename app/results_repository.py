"""Repository layer for the `models`, `runs`, and `results` tables (SCRUM-19).

Mirrors the pattern from `app/prompts/repository.py`: a `Protocol` that the
runner depends on, and a `Postgres…` implementation. Tests swap in a
`FakeResultsRepository`.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Protocol

from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class DecisionRow:
    id: int
    recommended_model: str
    confidence: str
    determinant_metrics: list[str]
    tradeoffs: Optional[str]
    reasoning: str
    prompt_id: Optional[int]
    input_hash: str
    profile: Optional[str]
    weighted_scores: Optional[list]
    created_at: datetime
    run_id: Optional[int] = None


@dataclass(frozen=True)
class ModelRow:
    id: int
    provider: str
    name: str
    version: Optional[str]
    input_cost: Decimal
    output_cost: Decimal


class ModelNotFoundError(LookupError):
    """Raised when a model key has no matching row in the `models` table."""


class ResultsRepository(Protocol):
    """Interface the runner depends on for DB persistence."""

    def lookup_model(self, name: str) -> ModelRow: ...

    def create_run(self, prompt_id: int, dataset: str) -> int: ...

    def latest_run_id(self) -> Optional[int]: ...

    def update_judge(
        self, *, result_id: int, judge_score: Decimal, judge_reasoning: str
    ) -> None: ...

    def insert_result(
        self,
        *,
        run_id: int,
        model_id: int,
        case_id: str,
        question: Optional[str] = None,
        response: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cost: Decimal,
        prompt_style: Optional[str] = None,
        sample_idx: int = 0,
        resp_style_headers: Optional[int] = None,
        resp_style_bold: Optional[int] = None,
        resp_style_ordered: Optional[int] = None,
        resp_style_unordered: Optional[int] = None,
        resp_style_code_blocks: Optional[int] = None,
    ) -> int: ...

    def finalize_run(self, run_id: int) -> None: ...

    def find_decision(
        self, *, input_hash: str, prompt_id: Optional[int], profile: Optional[str]
    ) -> Optional[DecisionRow]: ...

    def insert_decision(
        self,
        *,
        recommended_model: str,
        confidence: str,
        determinant_metrics: list[str],
        tradeoffs: Optional[str],
        reasoning: str,
        prompt_id: Optional[int],
        input_hash: str,
        input_snapshot: Any,
        profile: Optional[str] = None,
        weighted_scores: Optional[list] = None,
        run_id: Optional[int] = None,
    ) -> DecisionRow: ...

    def latest_decision(self) -> Optional[DecisionRow]: ...

    def fetch_decision_metrics(self, run_id: int) -> list[dict]: ...


class PostgresResultsRepository:
    """psycopg-backed implementation. Used by the runner CLI."""

    def __init__(self, conn):
        self.conn = conn

    def lookup_model(self, name: str) -> ModelRow:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, provider, name, version, input_cost, output_cost
                FROM models
                WHERE name = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (name,),
            )
            row = cur.fetchone()
        if row is None:
            raise ModelNotFoundError(
                f"No row in `models` table for name={name!r}. "
                "Did you run db/seed.sql?"
            )
        return ModelRow(
            id=row[0],
            provider=row[1],
            name=row[2],
            version=row[3],
            input_cost=row[4],
            output_cost=row[5],
        )

    def create_run(self, prompt_id: int, dataset: str) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO runs (prompt_id, dataset)
                VALUES (%s, %s)
                RETURNING id
                """,
                (prompt_id, dataset),
            )
            run_id = cur.fetchone()[0]
        self.conn.commit()
        return run_id

    def latest_run_id(self) -> Optional[int]:
        """Id of the most recent run, or None if no runs exist yet."""
        with self.conn.cursor() as cur:
            cur.execute("SELECT MAX(id) FROM runs")
            row = cur.fetchone()
        return row[0] if row else None

    def insert_result(
        self,
        *,
        run_id: int,
        model_id: int,
        case_id: str,
        question: Optional[str] = None,
        response: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cost: Decimal,
        prompt_style: Optional[str] = None,
        sample_idx: int = 0,
        resp_style_headers: Optional[int] = None,
        resp_style_bold: Optional[int] = None,
        resp_style_ordered: Optional[int] = None,
        resp_style_unordered: Optional[int] = None,
        resp_style_code_blocks: Optional[int] = None,
    ) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO results
                    (run_id, model_id, case_id, question, response,
                     latency_ms, input_tokens, output_tokens, cost,
                     prompt_style, sample_idx,
                     resp_style_headers, resp_style_bold, resp_style_ordered,
                     resp_style_unordered, resp_style_code_blocks)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    run_id,
                    model_id,
                    case_id,
                    question,
                    response,
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    cost,
                    prompt_style,
                    sample_idx,
                    resp_style_headers,
                    resp_style_bold,
                    resp_style_ordered,
                    resp_style_unordered,
                    resp_style_code_blocks,
                ),
            )
            returned_id = cur.fetchone()[0]
        self.conn.commit()
        return returned_id
    
    def update_judge(
        self,
        *,
        result_id: int,
        judge_score: Decimal,
        judge_reasoning: str,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                UPDATE results
                SET judge_score = %s, judge_reasoning = %s
                WHERE id = %s
                """,
                (judge_score, judge_reasoning, result_id)
            )
        self.conn.commit()

    def finalize_run(self, run_id: int) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET finished_at = NOW() WHERE id = %s",
                (run_id,),
            )
        self.conn.commit()

    # --- Final decision (SCRUM-38) -------------------------------------

    # `prompt_id IS NOT DISTINCT FROM %s` so a NULL prompt_id matches a NULL
    # lookup (plain `=` would never match NULLs) — keeps the cache correct even
    # if the prompt was never synced to the `prompts` table.
    # All SELECTs share this column list so DecisionRow mapping stays in sync.
    _DECISION_COLS = (
        "id, recommended_model, confidence, determinant_metrics, tradeoffs, "
        "reasoning, prompt_id, input_hash, profile, weighted_scores, created_at, "
        "run_id"
    )

    def find_decision(
        self, *, input_hash: str, prompt_id: Optional[int], profile: Optional[str]
    ) -> Optional[DecisionRow]:
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {self._DECISION_COLS}
                FROM decisions
                WHERE input_hash = %s
                  AND prompt_id IS NOT DISTINCT FROM %s
                  AND profile IS NOT DISTINCT FROM %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (input_hash, prompt_id, profile),
            )
            return self._decision_row(cur.fetchone())

    def insert_decision(
        self,
        *,
        recommended_model: str,
        confidence: str,
        determinant_metrics: list[str],
        tradeoffs: Optional[str],
        reasoning: str,
        prompt_id: Optional[int],
        input_hash: str,
        input_snapshot: Any,
        profile: Optional[str] = None,
        weighted_scores: Optional[list] = None,
        run_id: Optional[int] = None,
    ) -> DecisionRow:
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                INSERT INTO decisions
                    (recommended_model, confidence, determinant_metrics,
                     tradeoffs, reasoning, prompt_id, input_hash, input_snapshot,
                     profile, weighted_scores, run_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING {self._DECISION_COLS}
                """,
                (
                    recommended_model,
                    confidence,
                    Jsonb(determinant_metrics),
                    tradeoffs,
                    reasoning,
                    prompt_id,
                    input_hash,
                    Jsonb(input_snapshot),
                    profile,
                    Jsonb(weighted_scores) if weighted_scores is not None else None,
                    run_id,
                ),
            )
            row = self._decision_row(cur.fetchone())
        self.conn.commit()
        return row  # type: ignore[return-value]

    def latest_decision(self) -> Optional[DecisionRow]:
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT {self._DECISION_COLS}
                FROM decisions
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            )
            return self._decision_row(cur.fetchone())

    def fetch_decision_metrics(self, run_id: int) -> list[dict]:
        """Read the `model_decision_metrics` view for ONE run, as plain dicts.

        Scoped to `run_id` so a decision reflects only the models in that test
        (not every model ever judged). Decimals are kept as-is; app/decision.py
        canonicalises them for hashing.
        """
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM model_decision_metrics WHERE run_id = %s "
                "ORDER BY model",
                (run_id,),
            )
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    @staticmethod
    def _decision_row(r) -> Optional[DecisionRow]:
        if r is None:
            return None
        return DecisionRow(
            id=r[0],
            recommended_model=r[1],
            confidence=r[2],
            determinant_metrics=r[3],
            tradeoffs=r[4],
            reasoning=r[5],
            prompt_id=r[6],
            input_hash=r[7],
            profile=r[8],
            weighted_scores=r[9],
            created_at=r[10],
            run_id=r[11],
        )
