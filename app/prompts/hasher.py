"""
Content hashing for prompt versioning (SCRUM-18).

The hash IS the identity of a prompt version. Two prompts with the
same hash MUST mean "semantically identical content" — otherwise
edits like a trailing space or a CRLF/LF flip would silently fork
the history.

Normalization rules (applied before SHA-256) :
  1. Unicode NFC normalization (accents composed the same way).
  2. CRLF / CR → LF (no editor-induced version drift).
  3. Strip trailing whitespace from each line.
  4. Strip leading / trailing whitespace from the whole content.

These rules are PART OF THE CONTRACT. Changing them invalidates
every hash already stored in the database, so any future change
must be accompanied by a re-sync of all prompts and a migration
note.
"""

from __future__ import annotations

import hashlib
import unicodedata


def normalize_content(content: str) -> str:
    """Apply the documented normalization rules to a prompt's content."""
    content = unicodedata.normalize("NFC", content)
    content = content.replace("\r\n", "\n").replace("\r", "\n")
    content = "\n".join(line.rstrip() for line in content.split("\n"))
    return content.strip()


def compute_hash(content: str) -> str:
    """SHA-256 (hex, 64 chars) of the normalized content."""
    return hashlib.sha256(normalize_content(content).encode("utf-8")).hexdigest()
