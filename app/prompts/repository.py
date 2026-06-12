"""
Repository layer for the `prompts` table (SCRUM-18).

The sync logic and the CLI talk to PostgreSQL through this class
only — so swapping psycopg for SQLAlchemy later, or stubbing it
out in tests, stays a one-spot change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Protocol


@dataclass(frozen=True)
class PromptRow:
    id: int
    name: str
    version: str
    content: str
    hash: str
    previous_version_id: Optional[int]
    created_at: datetime


class PromptRepository(Protocol):
    """Interface a prompt repository must satisfy."""

    def find_by_name_and_hash(self, name: str, hash_: str) -> Optional[PromptRow]: ...

    def latest_by_name(self, name: str) -> Optional[PromptRow]: ...

    def insert(
        self,
        name: str,
        content: str,
        version: str,
        hash_: str,
        previous_version_id: Optional[int],
    ) -> PromptRow: ...

    def history(self, name: str) -> list[PromptRow]: ...

    def list_names(self) -> list[str]: ...


class PostgresPromptRepository:
    """psycopg-backed implementation. Used by the CLI and the runner."""

    def __init__(self, conn):
        self.conn = conn

    def find_by_name_and_hash(self, name: str, hash_: str) -> Optional[PromptRow]:
        row = self._fetch_one(
            """
            SELECT id, name, version, content, hash, previous_version_id, created_at
            FROM prompts
            WHERE name = %s AND hash = %s
            """,
            (name, hash_),
        )
        return self._row(row)

    def latest_by_name(self, name: str) -> Optional[PromptRow]:
        row = self._fetch_one(
            """
            SELECT id, name, version, content, hash, previous_version_id, created_at
            FROM prompts
            WHERE name = %s
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (name,),
        )
        return self._row(row)

    def insert(
        self,
        name: str,
        content: str,
        version: str,
        hash_: str,
        previous_version_id: Optional[int],
    ) -> PromptRow:
        row = self._fetch_one(
            """
            INSERT INTO prompts (name, content, version, hash, previous_version_id)
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id, name, version, content, hash, previous_version_id, created_at
            """,
            (name, content, version, hash_, previous_version_id),
        )
        self.conn.commit()
        return self._row(row)  # type: ignore[return-value]

    def history(self, name: str) -> list[PromptRow]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, name, version, content, hash, previous_version_id, created_at
                FROM prompts
                WHERE name = %s
                ORDER BY created_at ASC, id ASC
                """,
                (name,),
            )
            return [self._row(r) for r in cur.fetchall()]  # type: ignore[misc]

    def list_names(self) -> list[str]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT DISTINCT name FROM prompts ORDER BY name")
            return [r[0] for r in cur.fetchall()]

    def _fetch_one(self, sql: str, params: tuple):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()

    @staticmethod
    def _row(r) -> Optional[PromptRow]:
        if r is None:
            return None
        return PromptRow(
            id=r[0],
            name=r[1],
            version=r[2],
            content=r[3],
            hash=r[4],
            previous_version_id=r[5],
            created_at=r[6],
        )
