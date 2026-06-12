"""
Sync orchestration (SCRUM-18, DoD #4).

Reads every prompt YAML in a directory, hashes its content, and
ensures the database reflects the current state :
  - If (name, hash) already exists → nothing happens.
  - If `name` exists but `hash` differs → a NEW version row is
    inserted, linked via previous_version_id to the latest row.
  - If `name` is new → an initial row is inserted with
    previous_version_id = NULL (root version).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .hasher import compute_hash
from .loader import Prompt, load_all
from .repository import PromptRepository, PromptRow


class SyncAction(str, Enum):
    UNCHANGED = "unchanged"
    NEW_VERSION = "new_version"
    INITIAL = "initial"


@dataclass(frozen=True)
class SyncEntry:
    prompt: Prompt
    action: SyncAction
    row: PromptRow
    hash: str


@dataclass(frozen=True)
class SyncReport:
    entries: list[SyncEntry]

    @property
    def inserted(self) -> list[SyncEntry]:
        return [e for e in self.entries if e.action != SyncAction.UNCHANGED]

    @property
    def unchanged(self) -> list[SyncEntry]:
        return [e for e in self.entries if e.action == SyncAction.UNCHANGED]


def sync_prompts(directory: Path, repo: PromptRepository) -> SyncReport:
    """Idempotent sync : safe to call repeatedly."""
    entries: list[SyncEntry] = []
    for prompt in load_all(directory):
        h = compute_hash(prompt.content)

        existing = repo.find_by_name_and_hash(prompt.name, h)
        if existing is not None:
            entries.append(SyncEntry(prompt, SyncAction.UNCHANGED, existing, h))
            continue

        latest = repo.latest_by_name(prompt.name)
        previous_id = latest.id if latest is not None else None
        row = repo.insert(prompt.name, prompt.content, prompt.version, h, previous_id)
        action = SyncAction.NEW_VERSION if latest is not None else SyncAction.INITIAL
        entries.append(SyncEntry(prompt, action, row, h))

    return SyncReport(entries=entries)
