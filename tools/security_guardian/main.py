import argparse
import json
import subprocess
import sys
from pathlib import Path

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.security_guardian.remediate import remediate
from tools.security_guardian.report import build_report


def sh(cmd):
    return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)


def read_json(path):
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="scan")
    ap.add_argument("--inputs", nargs="*", default=[])
    args = ap.parse_args()

    scans = {Path(x).name: read_json(x) for x in args.inputs}

    # Try remediation (safe-only)
    changes = remediate(scans)

    # Write report for PR body / audit
    report_text = build_report(scans, changes)
    Path("security_reports").mkdir(exist_ok=True)
    Path("security_reports/last_report.md").write_text(report_text, encoding="utf-8")

    # If changed files, also tighten basic ignores
    # (No auto-adding allowlists for gitleaks unless user does it manually)
    sh(["bash", "-lc", "git status --porcelain=v1"])


if __name__ == "__main__":
    main()
