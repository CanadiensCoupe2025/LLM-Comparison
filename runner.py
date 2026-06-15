"""Repo-root entry point so `python runner.py --dataset ...` works as
documented in ARCHITECTURE.md §5.4. All logic lives in `app/runner.py`."""
import sys

from app.runner import main

if __name__ == "__main__":
    sys.exit(main())
