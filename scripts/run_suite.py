"""Missing acceptance suites fail explicitly, never become green placeholder evidence."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("suite", choices=["browser", "browser-installed", "native"])
parser.add_argument("--selector", default="")
args = parser.parse_args()
if args.suite != "browser" and not args.selector:
    raise SystemExit("Missing PROFILE/BROWSER. No native/browser acceptance was executed.")
if args.suite == "browser-installed" and args.selector not in {"brave", "edge"}:
    raise SystemExit("BROWSER must be brave or edge")
if args.suite == "native":
    raise SystemExit(
        subprocess.run(
            [sys.executable, str(ROOT / "scripts/run_native.py"), args.selector], cwd=ROOT
        ).returncode
    )
manifest = json.loads((ROOT / "package.json").read_text())
script = f"test:{args.suite}"
if script not in manifest.get("scripts", {}):
    raise SystemExit(
        f"Missing {args.suite} acceptance suite ({script}). No acceptance evidence produced."
    )
selector = [f"--project={args.selector}-*"] if args.suite == "browser-installed" else []
raise SystemExit(subprocess.run(["npm", "run", script, "--", *selector], cwd=ROOT).returncode)
