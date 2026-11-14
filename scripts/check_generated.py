"""Regenerate TS in memory and compare without touching the working tree."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
result = subprocess.run(["node", "scripts/generate-types.mjs", "--check"], cwd=ROOT, check=False)
raise SystemExit(result.returncode)
