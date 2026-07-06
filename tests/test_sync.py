"""
Sync orchestration tests — exercise the DoD #4 guarantee
("a modified prompt automatically creates a new version") without
needing a live PostgreSQL.
"""

from datetime import datetime
from pathlib import Path

from app.prompts.hasher import compute_hash
from app.prompts.repository import PromptRow
from app.prompts.sync import SyncAction, sync_prompts


class FakeRepo:
    """In-memory PromptRepository — list-of-dicts pretending to be a table."""

    def __init__(self):
        self.rows: list[PromptRow] = []
        self._next_id = 1

    def find_by_name_and_hash(self, name, hash_):
        for r in self.rows:
            if r.name == name and r.hash == hash_:
                return r
        return None

    def latest_by_name(self, name):
        matching = [r for r in self.rows if r.name == name]
        if not matching:
            return None
        return sorted(matching, key=lambda r: (r.created_at, r.id))[-1]

    def insert(self, name, content, version, hash_, previous_version_id):
        row = PromptRow(
            id=self._next_id,
            name=name,
            version=version,
            content=content,
            hash=hash_,
            previous_version_id=previous_version_id,
            created_at=datetime.now(),
        )
        self._next_id += 1
        self.rows.append(row)
        return row

    def history(self, name):
        return [r for r in self.rows if r.name == name]

    def list_names(self):
        return sorted({r.name for r in self.rows})


def _write(path: Path, name: str, version: str, content: str) -> Path:
    path.write_text(f"name: {name}\nversion: {version}\ncontent: |\n  {content}\n", encoding="utf-8")
    return path


def test_initial_sync_inserts_each_prompt_once(tmp_path):
    _write(tmp_path / "a.yaml", "judge_rubric", "1.0", "Tu es un evaluateur.")
    _write(tmp_path / "b.yaml", "eval_system", "1.0", "Reponds en francais.")

    repo = FakeRepo()
    report = sync_prompts(tmp_path, repo)

    assert len(report.inserted) == 2
    assert all(e.action == SyncAction.INITIAL for e in report.inserted)
    assert all(e.row.previous_version_id is None for e in report.inserted)


def test_sync_is_idempotent(tmp_path):
    _write(tmp_path / "a.yaml", "judge_rubric", "1.0", "Tu es un evaluateur.")
    repo = FakeRepo()

    first = sync_prompts(tmp_path, repo)
    second = sync_prompts(tmp_path, repo)

    assert len(first.inserted) == 1
    assert len(second.inserted) == 0
    assert len(second.unchanged) == 1
    assert len(repo.rows) == 1


def test_modified_content_creates_new_version(tmp_path):
    yaml_path = tmp_path / "a.yaml"
    _write(yaml_path, "judge_rubric", "1.0", "Tu es un evaluateur.")
    repo = FakeRepo()
    sync_prompts(tmp_path, repo)

    _write(yaml_path, "judge_rubric", "1.1", "Tu es un evaluateur strict.")
    report = sync_prompts(tmp_path, repo)

    assert len(report.inserted) == 1
    entry = report.inserted[0]
    assert entry.action == SyncAction.NEW_VERSION
    assert entry.row.previous_version_id == 1
    assert len(repo.rows) == 2
    assert repo.rows[0].hash != repo.rows[1].hash


def test_cosmetic_edits_do_not_create_new_version(tmp_path):
    yaml_path = tmp_path / "a.yaml"
    _write(yaml_path, "judge_rubric", "1.0", "Tu es un evaluateur.")
    repo = FakeRepo()
    sync_prompts(tmp_path, repo)

    # Same content but with trailing whitespace and CRLF — hash must absorb it.
    yaml_path.write_text(
        "name: judge_rubric\r\nversion: 1.0\r\ncontent: |\r\n  Tu es un evaluateur.   \r\n",
        encoding="utf-8",
    )
    report = sync_prompts(tmp_path, repo)

    assert len(report.inserted) == 0
    assert len(report.unchanged) == 1
    assert len(repo.rows) == 1


def test_chained_versions_link_to_their_predecessor(tmp_path):
    yaml_path = tmp_path / "a.yaml"
    repo = FakeRepo()

    _write(yaml_path, "judge_rubric", "1.0", "Note entre 0 et 1.")
    sync_prompts(tmp_path, repo)
    _write(yaml_path, "judge_rubric", "1.1", "Note entre 0 et 1 strictement.")
    sync_prompts(tmp_path, repo)
    _write(yaml_path, "judge_rubric", "2.0", "Note la reponse sur 5.")
    sync_prompts(tmp_path, repo)

    rows = repo.history("judge_rubric")
    assert len(rows) == 3
    assert rows[0].previous_version_id is None
    assert rows[1].previous_version_id == rows[0].id
    assert rows[2].previous_version_id == rows[1].id


def test_hash_matches_expected_sha256(tmp_path):
    _write(tmp_path / "a.yaml", "judge", "1.0", "hello")
    repo = FakeRepo()
    report = sync_prompts(tmp_path, repo)

    expected = compute_hash("hello")
    assert report.inserted[0].hash == expected
    assert report.inserted[0].row.hash == expected
