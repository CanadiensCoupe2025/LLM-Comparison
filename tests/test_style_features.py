"""Unit tests for response-style feature extraction (pure, no IO)."""
from __future__ import annotations

from app.style_features import StyleFeatures, extract_style_features


def test_counts_headers_all_levels():
    text = "# H1\n## H2\n### H3\n#### H4\n##### H5\n###### H6\n"
    assert extract_style_features(text).headers == 6


def test_hashes_without_space_are_not_headers():
    # '#tag' and a 7-hash line are not valid markdown headers.
    assert extract_style_features("#notaheader\n####### too many\n").headers == 0


def test_counts_bold_both_syntaxes():
    feats = extract_style_features("**bold one** and __bold two__ plus **three**")
    assert feats.bold == 3


def test_counts_ordered_and_unordered_lists():
    text = (
        "1. first\n"
        "2. second\n"
        "- bullet a\n"
        "* bullet b\n"
        "+ bullet c\n"
    )
    feats = extract_style_features(text)
    assert feats.ordered == 2
    assert feats.unordered == 3


def test_code_blocks_counted_and_stripped_before_prose_counts():
    # The markdown inside the fence must NOT count toward headers/bold/lists.
    text = (
        "Here is code:\n"
        "```\n"
        "# this is a python comment, not a header\n"
        "1. not a list\n"
        "**not bold**\n"
        "```\n"
        "# Real Header\n"
        "**real bold**\n"
    )
    feats = extract_style_features(text)
    assert feats.code_blocks == 1
    assert feats.headers == 1   # only the real one outside the fence
    assert feats.bold == 1
    assert feats.ordered == 0


def test_plain_prose_has_zero_features():
    assert extract_style_features("Just a plain sentence with no markdown.") == \
        StyleFeatures(headers=0, bold=0, ordered=0, unordered=0, code_blocks=0)


def test_empty_and_none_safe():
    assert extract_style_features("") == StyleFeatures(0, 0, 0, 0, 0)
    assert extract_style_features(None) == StyleFeatures(0, 0, 0, 0, 0)
