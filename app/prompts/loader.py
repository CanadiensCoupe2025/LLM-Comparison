"""
YAML loader for prompt templates (SCRUM-18).

Each prompt is a single YAML file with three required fields :

    name    : str        — stable identifier (kebab_case or snake_case)
    version : str | int  — human-readable version label (cast to str)
    content : str        — the actual prompt text (multi-line OK)

Optional metadata is allowed and ignored at this layer.

The loader is intentionally schema-light : the hasher is what
guarantees content integrity, not the YAML structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    content: str
    source_path: Path
    metadata: dict[str, Any]


class PromptValidationError(ValueError):
    pass


def _require(data: dict[str, Any], key: str, path: Path) -> Any:
    if key not in data:
        raise PromptValidationError(f"{path}: missing required field '{key}'")
    return data[key]


def load_prompt(path: Path) -> Prompt:
    """Load one YAML file and return a validated Prompt."""
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise PromptValidationError(f"{path}: root must be a YAML mapping")

    name = _require(data, "name", path)
    version = _require(data, "version", path)
    content = _require(data, "content", path)

    if not isinstance(name, str) or not name.strip():
        raise PromptValidationError(f"{path}: 'name' must be a non-empty string")
    if not isinstance(version, (str, int, float)):
        raise PromptValidationError(f"{path}: 'version' must be a string or number")
    if not isinstance(content, str) or not content.strip():
        raise PromptValidationError(f"{path}: 'content' must be a non-empty string")

    metadata = {k: v for k, v in data.items() if k not in ("name", "version", "content")}

    return Prompt(
        name=name.strip(),
        version=str(version),
        content=content,
        source_path=path,
        metadata=metadata,
    )


def load_all(directory: Path) -> list[Prompt]:
    """Recursively load every *.yaml in `directory`, sorted by path for determinism."""
    if not directory.exists():
        raise FileNotFoundError(f"Prompt directory not found: {directory}")
    files = sorted(directory.rglob("*.yaml"))
    return [load_prompt(p) for p in files]
