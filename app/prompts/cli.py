"""
Small CLI to operate on versioned prompts (SCRUM-18, DoD #3).

Usage examples (inside the app container) :
    python -m app.prompts.cli sync
    python -m app.prompts.cli list
    python -m app.prompts.cli history judge_rubric
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import psycopg

from app.logging_setup import configure_logging
from .repository import PostgresPromptRepository
from .sync import sync_prompts, SyncAction


DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("DATABASE_URL is not set — check your .env or compose env_file.")
    return psycopg.connect(url)


def _cmd_sync(args: argparse.Namespace) -> int:
    directory = Path(args.directory).resolve()
    with _connect() as conn:
        repo = PostgresPromptRepository(conn)
        report = sync_prompts(directory, repo)
    for e in report.entries:
        marker = {
            SyncAction.UNCHANGED:   "  ",
            SyncAction.NEW_VERSION: "+ ",
            SyncAction.INITIAL:     "* ",
        }[e.action]
        print(f"{marker}{e.prompt.name:30s} v{e.prompt.version:8s} {e.hash[:12]}  {e.action.value}")
    print(f"\n{len(report.inserted)} inserted, {len(report.unchanged)} unchanged.")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    with _connect() as conn:
        repo = PostgresPromptRepository(conn)
        for name in repo.list_names():
            latest = repo.latest_by_name(name)
            assert latest is not None
            print(f"{name:30s} latest v{latest.version}  {latest.hash[:12]}  ({latest.created_at:%Y-%m-%d %H:%M})")
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    with _connect() as conn:
        repo = PostgresPromptRepository(conn)
        rows = repo.history(args.name)
    if not rows:
        print(f"No prompt named '{args.name}' in the database.")
        return 1
    print(f"History for '{args.name}' :")
    for row in rows:
        prev = row.previous_version_id if row.previous_version_id is not None else "—"
        print(
            f"  id={row.id:4d}  v{row.version:8s}  hash={row.hash[:12]}  "
            f"prev={prev}  at {row.created_at:%Y-%m-%d %H:%M:%S}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prompts", description="Versioned prompt management.")
    sub = parser.add_subparsers(dest="command", required=True)

    sync = sub.add_parser("sync", help="Sync YAML templates into the DB (idempotent).")
    sync.add_argument("--directory", default=str(DEFAULT_TEMPLATES_DIR),
                      help=f"Templates directory (default: {DEFAULT_TEMPLATES_DIR})")
    sync.set_defaults(func=_cmd_sync)

    lst = sub.add_parser("list", help="List all prompt names with their latest version.")
    lst.set_defaults(func=_cmd_list)

    hist = sub.add_parser("history", help="Show the version chain for one prompt.")
    hist.add_argument("name", help="Prompt name (as defined in YAML).")
    hist.set_defaults(func=_cmd_history)

    args = parser.parse_args(argv)
    configure_logging()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
