import argparse
import os
import subprocess
import json
import textwrap
import time


def sh(cmd, check=False):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(p.stdout)
    return p.returncode, p.stdout


def collect_context():
    ctx = {}
    _, ctx["git_status"] = sh(["bash", "-lc", "git status --porcelain=v1"])
    _, ctx["last_ci_guess"] = sh(["bash", "-lc", "pytest -q 2>&1 || true"])
    _, ctx["ruff_guess"] = sh(["bash", "-lc", "ruff check . 2>&1 || true"])
    return ctx


def llm_enabled():
    return bool(os.getenv("GUARDIAN_LLM_PROVIDER")) and bool(os.getenv("GUARDIAN_LLM_BASE_URL")) and (
        os.getenv("GUARDIAN_LLM_API_KEY") or os.getenv("GUARDIAN_LLM_PROVIDER") == "ollama"
    )


def call_llm(prompt: str) -> str:
    try:
        import requests
    except ModuleNotFoundError as exc:
        raise RuntimeError("requests is required for LLM calls (pip install requests)") from exc

    provider = os.getenv("GUARDIAN_LLM_PROVIDER", "")
    base = os.getenv("GUARDIAN_LLM_BASE_URL", "").rstrip("/")
    key = os.getenv("GUARDIAN_LLM_API_KEY", "")
    model = os.getenv("GUARDIAN_LLM_MODEL", "llama3:8b")

    if provider == "ollama":
        # Ollama generate
        r = requests.post(
            f"{base}/api/generate", json={"model": model, "prompt": prompt, "stream": False}, timeout=60
        )
        r.raise_for_status()
        return r.json().get("response", "")

    # OpenAI-compatible chat completions (minimal)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
    r = requests.post(f"{base}/chat/completions", headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    data = r.json()
    return data["choices"][0]["message"]["content"]


def apply_unified_diff(diff_text: str):
    # expects a unified diff; apply via git apply
    p = subprocess.run(
        ["bash", "-lc", "git apply -"], input=diff_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
    )
    return p.returncode, p.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="ci-failure")
    args = ap.parse_args()

    ctx = collect_context()

    if not llm_enabled():
        # no LLM: just write a report file for manual fix
        report = {"mode": args.mode, "note": "LLM not configured. Collected context only.", "context": ctx}
        os.makedirs("guardian_reports", exist_ok=True)
        with open(f"guardian_reports/report_{int(time.time())}.json", "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        return

    prompt = textwrap.dedent(
        f"""
    You are a code repair agent. Output ONLY a unified diff patch (git apply compatible). No explanations.
    Fix failing tests/lint for this repo.

    Context:
    - pytest output (best-effort):
    {ctx["last_ci_guess"][:6000]}

    - ruff output (best-effort):
    {ctx["ruff_guess"][:4000]}

    Requirements:
    - Minimal change
    - Do not add new heavy deps
    - Keep behavior intact
    - If cannot fix, output an empty diff
    """
    )

    diff = call_llm(prompt).strip()
    if not diff or "diff --git" not in diff:
        return

    code, out = apply_unified_diff(diff)
    if code != 0:
        # if patch failed, leave a report
        os.makedirs("guardian_reports", exist_ok=True)
        with open(f"guardian_reports/patch_failed_{int(time.time())}.txt", "w", encoding="utf-8") as f:
            f.write(out + "\n\n---PATCH---\n" + diff)
        return

    # validate
    sh(["bash", "-lc", "ruff check . || true"])
    sh(["bash", "-lc", "pytest -q || true"])


if __name__ == "__main__":
    main()
