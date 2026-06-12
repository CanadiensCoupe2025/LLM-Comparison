from pathlib import Path

import pytest

from app.prompts.loader import (
    Prompt,
    PromptValidationError,
    load_all,
    load_prompt,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


def test_load_prompt_minimal_valid(tmp_path):
    path = _write(
        tmp_path,
        "judge.yaml",
        "name: judge_rubric\nversion: 1.0\ncontent: |\n  Tu es un évaluateur.\n",
    )
    p = load_prompt(path)
    assert isinstance(p, Prompt)
    assert p.name == "judge_rubric"
    assert p.version == "1.0"
    assert "évaluateur" in p.content
    assert p.source_path == path
    assert p.metadata == {}


def test_load_prompt_captures_metadata(tmp_path):
    path = _write(
        tmp_path,
        "judge.yaml",
        "name: judge\nversion: 2\ncontent: Hello\nused_by: [runner]\nauthor: alice\n",
    )
    p = load_prompt(path)
    assert p.version == "2"
    assert p.metadata == {"used_by": ["runner"], "author": "alice"}


def test_load_prompt_rejects_missing_name(tmp_path):
    path = _write(tmp_path, "x.yaml", "version: 1\ncontent: x\n")
    with pytest.raises(PromptValidationError, match="missing required field 'name'"):
        load_prompt(path)


def test_load_prompt_rejects_missing_version(tmp_path):
    path = _write(tmp_path, "x.yaml", "name: x\ncontent: x\n")
    with pytest.raises(PromptValidationError, match="missing required field 'version'"):
        load_prompt(path)


def test_load_prompt_rejects_missing_content(tmp_path):
    path = _write(tmp_path, "x.yaml", "name: x\nversion: 1\n")
    with pytest.raises(PromptValidationError, match="missing required field 'content'"):
        load_prompt(path)


def test_load_prompt_rejects_blank_content(tmp_path):
    path = _write(tmp_path, "x.yaml", "name: x\nversion: 1\ncontent: '   '\n")
    with pytest.raises(PromptValidationError, match="content"):
        load_prompt(path)


def test_load_prompt_rejects_non_mapping_root(tmp_path):
    path = _write(tmp_path, "x.yaml", "- not\n- a\n- mapping\n")
    with pytest.raises(PromptValidationError, match="root must be a YAML mapping"):
        load_prompt(path)


def test_load_all_returns_sorted_results(tmp_path):
    _write(tmp_path, "b.yaml", "name: b\nversion: 1\ncontent: B\n")
    _write(tmp_path, "a.yaml", "name: a\nversion: 1\ncontent: A\n")
    results = load_all(tmp_path)
    assert [p.name for p in results] == ["a", "b"]


def test_load_all_is_recursive(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    _write(tmp_path, "top.yaml", "name: top\nversion: 1\ncontent: T\n")
    _write(sub, "nested.yaml", "name: nested\nversion: 1\ncontent: N\n")
    names = {p.name for p in load_all(tmp_path)}
    assert names == {"top", "nested"}


def test_load_all_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_all(tmp_path / "does-not-exist")
