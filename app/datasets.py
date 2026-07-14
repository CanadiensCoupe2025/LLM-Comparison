"""Loader for evaluator YAML datasets (SCRUM-19, SCRUM-35).

Supports both v1 and v2 dataset schemas — the runner only cares about
`id` and `prompt`; everything else (`expected`, `category`, `kind`,
`rationale`) is preserved but unused at this stage.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class DatasetError(ValueError):
    """Raised when a YAML dataset is malformed or missing required keys."""


@dataclass(frozen=True)
class Case:
    id: str
    prompt: str
    raw: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class Dataset:
    name: str
    version: int | str
    cases: list[Case]
    source_path: Path
    description: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


def load_dataset(path: Path) -> Dataset:
    """Parse a YAML dataset file and return a `Dataset`.

    Raises `DatasetError` if the file can't be parsed or required keys are
    missing — `dataset.name`, `dataset.version`, `cases`, and every case's
    `id` + `prompt`. `dataset.description` is optional (defaults to "").
    """
    path = Path(path)
    if not path.is_file():
        raise DatasetError(f"Dataset file not found: {path}")

    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise DatasetError(f"Invalid YAML in {path}: {e}") from e

    if not isinstance(doc, dict):
        raise DatasetError(f"{path}: top-level must be a mapping")

    header = doc.get("dataset")
    if not isinstance(header, dict):
        raise DatasetError(f"{path}: missing or invalid `dataset:` header")
    name = header.get("name")
    version = header.get("version")
    description = header.get("description", "")
    if not isinstance(name, str) or not name.strip():
        raise DatasetError(f"{path}: `dataset.name` must be a non-empty string")
    if version is None:
        raise DatasetError(f"{path}: `dataset.version` is required")
    if not isinstance(description, str):
        raise DatasetError(f"{path}: `dataset.description` must be a string")

    raw_cases = doc.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise DatasetError(f"{path}: `cases:` must be a non-empty list")

    cases: list[Case] = []
    seen_ids: set[str] = set()
    for i, item in enumerate(raw_cases):
        if not isinstance(item, dict):
            raise DatasetError(f"{path}: case #{i} is not a mapping")
        cid = item.get("id")
        prompt = item.get("prompt")
        if not isinstance(cid, str) or not cid.strip():
            raise DatasetError(f"{path}: case #{i} missing required `id`")
        if not isinstance(prompt, str) or not prompt.strip():
            raise DatasetError(f"{path}: case {cid!r} missing required `prompt`")
        if cid in seen_ids:
            raise DatasetError(f"{path}: duplicate case id {cid!r}")
        seen_ids.add(cid)
        cases.append(Case(id=cid, prompt=prompt, raw=item))

    return Dataset(
        name=name,
        version=version,
        cases=cases,
        source_path=path,
        description=description,
        raw=doc,
    )


def discover_datasets(directory: Path) -> list[Dataset]:
    """Load every valid `*.yaml` dataset in `directory`, sorted by filename.

    Files that fail to parse (`DatasetError` — a stray README, a malformed
    YAML) are skipped so one bad file never breaks a caller iterating the
    folder (e.g. the GUI's dataset picker). Mirrors the prompt loader's
    directory scan in app/prompts/loader.py; there is no other dataset-
    discovery helper in the codebase.
    """
    directory = Path(directory)
    datasets: list[Dataset] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            datasets.append(load_dataset(path))
        except DatasetError:
            continue
    return datasets
