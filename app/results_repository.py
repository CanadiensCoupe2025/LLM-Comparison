"""Repository layer for the `models`, `runs`, and `results` tables (SCRUM-19).

Mirrors the pattern from `app/prompts/repository.py`: a `Protocol` that the
runner depends on, and a `Postgres…` implementation. Tests swap in a
`FakeResultsRepository`.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Protocol


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

    def update_judge(
        self, *, result_id: int, judge_score: Decimal, judge_reasoning: str
    ) -> None: ...

    def insert_result(
        self,
        *,
        run_id: int,
        model_id: int,
        case_id: str,
        response: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cost: Decimal,
        prompt_style: Optional[str] = None,
    ) -> int: ...

    def finalize_run(self, run_id: int) -> None: ...


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

    def insert_result(
        self,
        *,
        run_id: int,
        model_id: int,
        case_id: str,
        response: str,
        latency_ms: int,
        input_tokens: int,
        output_tokens: int,
        cost: Decimal,
        prompt_style: Optional[str] = None,
    ) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO results
                    (run_id, model_id, case_id, response,
                     latency_ms, input_tokens, output_tokens, cost,
                     prompt_style)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    run_id,
                    model_id,
                    case_id,
                    response,
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    cost,
                    prompt_style,
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
