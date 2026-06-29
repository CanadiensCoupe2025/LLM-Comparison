from __future__ import annotations

from pathlib import Path

import pytest

from app.datasets import DatasetError, load_dataset

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_DATASETS = REPO_ROOT / "evaluator" / "datasets"


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "ds.yaml"
    p.write_text(body, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Loading real datasets shipped with the repo (smoke tests).
# ---------------------------------------------------------------------------


def test_regression_v1_loads_without_errors():
    ds = load_dataset(REAL_DATASETS / "regression_v1.yaml")
    assert ds.name == "regression_core"
    assert ds.cases, "v1 should have at least one case"
    assert all(c.id and c.prompt for c in ds.cases)


def test_regression_v2_loads_without_errors():
    ds = load_dataset(REAL_DATASETS / "regression_v2.yaml")
    assert ds.name == "regression_core"
    assert len(ds.cases) >= 1
    # No duplicate case ids in the shipped dataset.
    ids = [c.id for c in ds.cases]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# Synthetic happy-path
# ---------------------------------------------------------------------------


def test_minimal_valid_yaml_loads(tmp_path):
    path = _write(
        tmp_path,
        """
dataset:
  name: tiny
  version: 1
cases:
  - id: a
    prompt: "hello"
  - id: b
    prompt: "world"
""",
    )
    ds = load_dataset(path)
    assert ds.name == "tiny"
    assert ds.version == 1
    assert [c.id for c in ds.cases] == ["a", "b"]
    assert [c.prompt for c in ds.cases] == ["hello", "world"]
    assert ds.source_path == path


# ---------------------------------------------------------------------------
# Failure cases
# ---------------------------------------------------------------------------


def test_missing_file_raises():
    with pytest.raises(DatasetError, match="not found"):
        load_dataset(Path("/nonexistent/path/foo.yaml"))


def test_invalid_yaml_raises(tmp_path):
    path = _write(tmp_path, "dataset:\n  name: [unclosed list")
    with pytest.raises(DatasetError, match="Invalid YAML"):
        load_dataset(path)


def test_top_level_not_mapping_raises(tmp_path):
    path = _write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(DatasetError, match="top-level"):
        load_dataset(path)


def test_missing_dataset_header_raises(tmp_path):
    path = _write(tmp_path, "cases:\n  - id: a\n    prompt: hi\n")
    with pytest.raises(DatasetError, match="dataset:"):
        load_dataset(path)


def test_missing_dataset_name_raises(tmp_path):
    path = _write(
        tmp_path,
        "dataset:\n  version: 1\ncases:\n  - id: a\n    prompt: hi\n",
    )
    with pytest.raises(DatasetError, match="name"):
        load_dataset(path)


def test_empty_cases_raises(tmp_path):
    path = _write(
        tmp_path,
        "dataset:\n  name: x\n  version: 1\ncases: []\n",
    )
    with pytest.raises(DatasetError, match="cases"):
        load_dataset(path)


def test_case_without_id_raises(tmp_path):
    path = _write(
        tmp_path,
        "dataset:\n  name: x\n  version: 1\ncases:\n  - prompt: hi\n",
    )
    with pytest.raises(DatasetError, match="id"):
        load_dataset(path)


def test_case_without_prompt_raises(tmp_path):
    path = _write(
        tmp_path,
        "dataset:\n  name: x\n  version: 1\ncases:\n  - id: a\n",
    )
    with pytest.raises(DatasetError, match="prompt"):
        load_dataset(path)


def test_duplicate_case_ids_raise(tmp_path):
    path = _write(
        tmp_path,
        """
dataset:
  name: x
  version: 1
cases:
  - id: dupe
    prompt: hi
  - id: dupe
    prompt: world
""",
    )
    with pytest.raises(DatasetError, match="duplicate"):
        load_dataset(path)
