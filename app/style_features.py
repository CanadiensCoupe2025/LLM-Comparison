"""Response-style feature extraction (arena-hard style control, adapted).

Counts the markdown/formatting features of a model response so we can measure
whether the judge's quality scores are confounded by verbosity or formatting —
i.e. whether a model "wins" partly by being long or heavily formatted rather
than correct. Ported from arena-hard-auto's `utils/add_markdown_info.py`.

Pure and dependency-free (just `re`), mirroring `app/judge.parse_verdict`:
module-level compiled regexes, a frozen dataclass result, no IO. Length is NOT
counted here — `results.output_tokens` already captures it and is the canonical
length covariate for the style-control regression.

These are RESPONSE-style features (`resp_style_*`). Do not confuse with
`results.prompt_style` (SCRUM-37), which is the prompt *phrasing* style
(zero-shot/few-shot/…) — a different axis entirely.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Fenced code blocks ```...```. Counted as a feature (code-vs-prose signal) and
# stripped before the other counts so code contents don't inflate them — the
# same order arena-hard uses. DOTALL so a block can span multiple lines.
_CODE_BLOCK = re.compile(r"```[^`]*```", re.DOTALL)

# Markdown headers H1–H6 at line start: one-to-six '#' then whitespace.
_HEADER = re.compile(r"^#{1,6}\s", re.MULTILINE)

# Bold spans: **...** or __...__ (no inner delimiter / newline).
_BOLD = re.compile(r"\*\*[^*\n]+\*\*|__[^_\n]+__")

# Ordered list items: line start, optional indent, digits, '.', whitespace.
_ORDERED = re.compile(r"^\s*\d+\.\s", re.MULTILINE)

# Unordered list items: line start, optional indent, one of - * +, whitespace.
_UNORDERED = re.compile(r"^\s*[-*+]\s", re.MULTILINE)


@dataclass(frozen=True)
class StyleFeatures:
    """Markdown/formatting counts for one response. All non-negative ints."""

    headers: int
    bold: int
    ordered: int
    unordered: int
    code_blocks: int


def extract_style_features(response: str) -> StyleFeatures:
    """Count markdown features in `response`.

    Code blocks are counted first, then removed before counting headers, bold,
    and list items — so markdown *inside* code samples doesn't inflate the
    prose-formatting counts.
    """
    text = response or ""
    code_blocks = len(_CODE_BLOCK.findall(text))
    prose = _CODE_BLOCK.sub("", text)
    return StyleFeatures(
        headers=len(_HEADER.findall(prose)),
        bold=len(_BOLD.findall(prose)),
        ordered=len(_ORDERED.findall(prose)),
        unordered=len(_UNORDERED.findall(prose)),
        code_blocks=code_blocks,
    )
