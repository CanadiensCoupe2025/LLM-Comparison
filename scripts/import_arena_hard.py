"""Generate a balanced LLMeter dataset from arena-hard-auto v2.0 questions.

Reads arena-hard's `question.jsonl` and emits a curated, balanced subset as an
LLMeter YAML dataset (open-ended → judged with `--judge`). One-time generator;
the committed artifact is `evaluator/datasets/arena_hard_v2_subset.yaml`.

Source (Apache-2.0): https://github.com/lmarena/arena-hard-auto
Fetch the source file first, e.g.:
    gh api "repos/lmarena/arena-hard-auto/contents/data/arena-hard-v2.0/question.jsonl?ref=main" \
      --jq '.content' | base64 -d > /tmp/arena_v2_questions.jsonl

Then:
    python scripts/import_arena_hard.py --source /tmp/arena_v2_questions.jsonl

Selection is deterministic (sort by uid, take the first N per subcategory) so the
output is reproducible — no RNG.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import yaml

SOURCE_REPO = "https://github.com/lmarena/arena-hard-auto"
SOURCE_REF = "196f6b826783b3da7310e361a805fa36f0be83f3"  # main @ retrieval
SOURCE_PATH = "data/arena-hard-v2.0/question.jsonl"
DEFAULT_OUT = Path("evaluator/datasets/arena_hard_v2_subset.yaml")


class _LiteralStr(str):
    """Marker so multi-line prompts dump as readable YAML block scalars (|)."""


def _literal_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


yaml.add_representer(_LiteralStr, _literal_representer)


def select_cases(records: list[dict], per_subcategory: int) -> list[dict]:
    """Balanced, deterministic pick: first N per subcategory, sorted by uid."""
    by_sub: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        by_sub[rec["subcategory"]].append(rec)

    cases: list[dict] = []
    for sub in sorted(by_sub):
        chosen = sorted(by_sub[sub], key=lambda r: r["uid"])[:per_subcategory]
        for rec in chosen:
            prompt = rec["prompt"]
            cases.append({
                "id": rec["uid"],                 # stable, traceable to source
                "category": rec["subcategory"],   # coding | math | creative_writing
                "subcategory": rec["subcategory"],
                "kind": "main",                   # open-ended → quality metric
                "source": "arena-hard-v2.0",
                "prompt": _LiteralStr(prompt) if "\n" in prompt else prompt,
                "expected": {"check": "judge"},   # judged by the Gemini judge path
            })
    return cases


def build_document(cases: list[dict]) -> dict:
    return {
        "dataset": {
            "name": "arena_hard_v2_subset",
            "version": 1,
            "description": (
                "Curated balanced subset of arena-hard-auto v2.0 hard prompts "
                "(coding / math / creative_writing). Open-ended; run with --judge."
            ),
        },
        "cases": cases,
    }


HEADER = f"""\
# ─────────────────────────────────────────────────────────────
# arena_hard_v2_subset.yaml  ·  GENERATED — do not edit by hand
#   regenerate: python scripts/import_arena_hard.py --source <question.jsonl>
# ─────────────────────────────────────────────────────────────
# Prompts imported VERBATIM from arena-hard-auto (Apache-2.0).
#   source : {SOURCE_REPO}
#   file   : {SOURCE_PATH}
#   ref    : {SOURCE_REF}
# Each case `id` is the upstream `uid` for traceability. See
# licenses/arena-hard-NOTICE for attribution.
# ─────────────────────────────────────────────────────────────
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="import_arena_hard")
    parser.add_argument("--source", required=True, type=Path,
                        help="Path to arena-hard v2.0 question.jsonl.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help=f"Output YAML (default: {DEFAULT_OUT}).")
    parser.add_argument("--per-subcategory", type=int, default=17,
                        help="Cases per subcategory (default 17 → ~51 total).")
    args = parser.parse_args(argv)

    # Split on '\n' only — str.splitlines() also breaks on exotic Unicode line
    # boundaries ( etc.) that appear inside some prompts, corrupting JSONL.
    with args.source.open(encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    cases = select_cases(records, args.per_subcategory)
    doc = build_document(cases)

    body = yaml.dump(doc, sort_keys=False, allow_unicode=True, width=100)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(HEADER + body, encoding="utf-8")
    print(f"Wrote {len(cases)} cases to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
