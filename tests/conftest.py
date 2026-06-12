import sys
from pathlib import Path

# Make `app.prompts` importable when running pytest from the repo root.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
