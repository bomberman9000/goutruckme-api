from datetime import datetime


def count_findings(obj):
    if not obj:
        return 0
    if isinstance(obj, list):
        return len(obj)
    if isinstance(obj, dict):
        # semgrep: results
        if "results" in obj and isinstance(obj["results"], list):
            return len(obj["results"])
        # bandit: results
        if "results" in obj and isinstance(obj["results"], list):
            return len(obj["results"])
    return 0


def build_report(scans: dict, changes: dict) -> str:
    lines = []
    lines.append(f"# Security Guardian report ({datetime.utcnow().isoformat()}Z)")
    lines.append("")
    for name, data in scans.items():
        lines.append(f"- {name}: {count_findings(data)} findings")
    lines.append("")
    lines.append("## Automated changes")
    for k, v in changes.items():
        lines.append(f"- {k}: {v}")
    lines.append("")
    lines.append("## Notes")
    lines.append("- Secrets are NOT auto-fixed. If gitleaks finds secrets: rotate keys, purge history manually, add proper ignores.")
    lines.append("- Semgrep/Bandit are report-only in MVP.")
    return "\n".join(lines)
