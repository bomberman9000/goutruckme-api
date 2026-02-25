import re
from pathlib import Path


def bump_requirements(requirements_path: Path, vulns: list[dict]) -> bool:
    if not requirements_path.exists():
        return False
    text = requirements_path.read_text(encoding="utf-8").splitlines()
    changed = False

    # pip-audit JSON differs by version; be defensive
    # We only do minimal: if vuln has "name" and "fix_versions", pin to first fix version (>=)
    fixes = {}
    for v in vulns or []:
        name = v.get("name") or v.get("dependency", {}).get("name")
        fix_versions = v.get("fix_versions") or v.get("fix_versions", [])
        if not name:
            continue
        if isinstance(fix_versions, list) and fix_versions:
            fixes[name.lower()] = fix_versions[0]

    if not fixes:
        return False

    out = []
    for line in text:
        m = re.match(r"^\s*([A-Za-z0-9_\-]+)\s*([<>=!~]=?.*)?\s*$", line)
        if not m:
            out.append(line)
            continue
        pkg = m.group(1)
        key = pkg.lower()
        if key in fixes:
            ver = fixes[key]
            out.append(f"{pkg}>={ver}")
            changed = True
        else:
            out.append(line)

    if changed:
        requirements_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


def remediate(scans: dict) -> dict:
    changes = {"requirements_bumped": False}

    pip_audit = scans.get("pip_audit.json", {})
    vulns = []
    if isinstance(pip_audit, list):
        vulns = pip_audit
    elif isinstance(pip_audit, dict):
        vulns = pip_audit.get("dependencies") or pip_audit.get("vulnerabilities") or []

    changes["requirements_bumped"] = bump_requirements(Path("requirements.txt"), vulns)

    # Secrets remediation is NOT automatic (we never rewrite history / auto-delete)
    # Bandit/Semgrep auto-fix is limited; keep report-only for now.
    return changes
