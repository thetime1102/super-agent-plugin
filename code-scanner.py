#!/usr/bin/env python3
"""
code-scanner.py -- Super Agent Proactive Code Scanner v2
========================================================
Chỉ SCAN, không FIX. Report lên OpenClaw chat.

Event-Driven Flow:
  git commit → hook (async) → code-scanner.py
       ↓
  Layer 1: ESLint --format json → phân tích lỗi
  Layer 2: DeepSeek Logic Scan → Race/Memory/Transaction
       ↓
  Ghi .scan_report.json + stdout
       ↓
  OpenClaw session phát hiện report → ping anh Vinh

Usage:
  python code-scanner.py                          # Scan git diff (post-commit)
  python code-scanner.py --all                    # Scan toàn bộ project  
  python code-scanner.py --file src/service.ts    # Scan 1 file
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from typing import Optional

# ─── Paths ────────────────────────────────────────────────────────────────
WORKSPACE = r"C:\Users\tqv11\.openclaw\workspace"
DEV_DIR = os.path.join(WORKSPACE, "nhatvi-ecosystem-dev")
SUPER_AGENT_DIR = os.path.join(WORKSPACE, "super-agent-plugin")
REPORT_FILE = os.path.join(WORKSPACE, "memory", ".scan_report.json")


# ─── DeepSeek API Key Auto-Detect ─────────────────────────────────────────

def _get_deepseek_key() -> str:
    """Tự động tìm DEEPSEEK_API_KEY từ nhiều nguồn."""
    # 1. Environment variable
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key

    # 2. .env.dev (project dev config)
    for env_file in [
        os.path.join(DEV_DIR, ".env.dev"),
        os.path.join(DEV_DIR, ".env"),
        os.path.join(DEV_DIR, ".env.prod.local"),
    ]:
        if os.path.isfile(env_file):
            with open(env_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY"):
                        key = line.split("=", 1)[1].strip().strip("'\"")
                        if key:
                            return key

    # 3. OpenClaw config
    oc_config = os.path.join(os.environ.get("USERPROFILE", ""), ".openclaw", "openclaw.json")
    if os.path.isfile(oc_config):
        try:
            with open(oc_config, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # Check for deepseek key in config
            env_key = cfg.get("env", {}).get("DEEPSEEK_API_KEY", "")
            if env_key:
                return env_key
        except Exception:
            pass

    return ""


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 1: ESLint Scan (CHỈ SCAN, KHÔNG FIX)
# ═══════════════════════════════════════════════════════════════════════════

def run_eslint(files: list[str]) -> dict:
    """Chạy ESLint --format json, trả về parsed results. KHÔNG auto-fix."""
    results = {"errors": [], "warnings": [], "auto_fixable": []}
    if not files:
        return results

    try:
        cmd = ["npx.cmd", "eslint", "--format", "json", "--max-warnings", "999"] + files
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, cwd=DEV_DIR,
            encoding="utf-8", errors="replace",
        )
        if proc.returncode not in (0, 1):
            return results

        parsed = json.loads(proc.stdout.strip() or "[]")
        for file_result in parsed:
            fp = file_result.get("filePath", "")
            for msg in file_result.get("messages", []):
                entry = {
                    "file": fp,
                    "line": msg.get("line", 0),
                    "col": msg.get("column", 0),
                    "end_line": msg.get("endLine", msg.get("line", 0)),
                    "end_col": msg.get("endColumn", msg.get("column", 0)),
                    "rule": msg.get("ruleId", "unknown"),
                    "message": msg.get("message", ""),
                    "severity": msg.get("severity", 2),
                }
                if entry["severity"] == 2:
                    results["errors"].append(entry)
                else:
                    results["warnings"].append(entry)

    except FileNotFoundError:
        print("   [eslint] Not found. Run: npm install")
    except json.JSONDecodeError:
        pass
    except subprocess.TimeoutExpired:
        print("   [eslint] Timeout")
    except Exception as e:
        print(f"   [eslint] {e}")

    return results


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 2: DeepSeek Logic Scan
# ═══════════════════════════════════════════════════════════════════════════

def get_git_diff_text(git_dir: str, since_sha: str = "") -> str:
    if not os.path.isdir(os.path.join(git_dir, ".git")):
        return ""
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, timeout=15, cwd=git_dir)
        current = r.stdout.decode("utf-8", errors="replace").strip()
        if not current:
            return ""
        base = since_sha if since_sha else f"{current}~1"
        r = subprocess.run(
            ["git", "diff", f"{base}..{current}", "--", "*.ts", "*.tsx"],
            capture_output=True, timeout=30, cwd=git_dir,
        )
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def call_deepseek(prompt: str, api_key: str) -> str:
    """Call DeepSeek API. Returns response text or error message."""
    import urllib.request, urllib.error

    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": (
                "You are a senior TypeScript code reviewer. Analyze diffs for LOGIC BUGS ONLY. "
                "Ignore syntax, style, formatting. Focus on: race conditions, memory leaks, "
                "missing transactions, async safety. Be concise."
            )},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1024,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"!! DeepSeek API {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return f"!! DeepSeek error: {e}"


def analyze_logic(diff_text: str, api_key: str) -> list[dict]:
    """LLM phân tích diff tìm lỗi logic. Trả về list issues."""
    if not diff_text.strip() or not api_key:
        return []

    prompt = f"""Analyze this git diff for LOGIC BUGS ONLY (ignore syntax/style):

1. RACE CONDITION: Concurrent DB writes? Shared mutable state without lock?
2. MEMORY LEAK: setInterval/addEventListener without cleanup? useEffect missing return?
3. MISSING TRANSACTION: Sequential DB writes without BEGIN/COMMIT/transaction wrapper?
4. ASYNC SAFETY: Promise.all on dependent ops? Missing await in critical path?

DIFF:
```
{diff_text[:6000]}
```

Respond JSON array ONLY:
[
  {{"type":"race_condition|memory_leak|missing_transaction|async_safety",
    "severity":"critical|warning",
    "file":"path.ts",
    "line":42,
    "description":"...",
    "suggestion":"..."}}
]
If clean, respond: []"""

    response = call_deepseek(prompt, api_key)
    if response.startswith("!!"):
        return [{"type": "llm_error", "severity": "warning", "description": response}]

    try:
        match = re.search(r'\[\s*\{.*\}\s*\]', response, re.DOTALL)
        if match:
            return json.loads(match.group())
        return []
    except Exception:
        return [{"type": "parse_error", "severity": "info", "description": response[:200]}]


# ═══════════════════════════════════════════════════════════════════════════
# REPORT -- Ghi file để OpenClaw session phát hiện và báo cáo
# ═══════════════════════════════════════════════════════════════════════════

def write_report(eslint: dict, logic: list[dict], files_scanned: list[str]):
    """Ghi scan report vào .scan_report.json -- OpenClaw sẽ đọc và ping."""
    critical_logic = [i for i in logic if i.get("severity") == "critical"]
    report = {
        "timestamp": time.time(),
        "datetime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files_scanned": files_scanned[:10],
        "eslint": {
            "errors": len(eslint["errors"]),
            "warnings": len(eslint["warnings"]),
            "details": eslint["errors"][:5] + eslint["warnings"][:3],
        },
        "logic": {
            "total": len(logic),
            "critical": len(critical_logic),
            "details": logic[:5],
        },
        "has_issues": len(eslint["errors"]) > 0 or len(critical_logic) > 0,
    }

    os.makedirs(os.path.dirname(REPORT_FILE), exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    return report


def print_report(report: dict):
    """In report ra console."""
    print("\n## Scan Report:")
    print(f"   Files: {report['files_scanned'][:3]}")
    print(f"   ESLint: {report['eslint']['errors']} err / {report['eslint']['warnings']} warn")
    print(f"   Logic: {report['logic']['total']} issues ({report['logic']['critical']} critical)")

    if report["has_issues"]:
        print(f"\n!!  Issues found! Report saved to: {REPORT_FILE}")


# ═══════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════

def scan(scan_all: bool = False, single_file: str = ""):
    """Main scan pipeline: ESLint → LLM Logic → Report. KHÔNG auto-fix."""
    print("## Code Scanner v2 -- Scan only, no auto-fix\n")

    # Step 0: Determine files
    files = []
    if single_file:
        files = [single_file]
    elif scan_all:
        files = ["src/", "web/src/"]
    else:
        r = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1..HEAD", "--", "*.ts", "*.tsx", "*.js"],
            capture_output=True, timeout=15, cwd=DEV_DIR,
        )
        files = [f.strip() for f in r.stdout.decode("utf-8", errors="replace").split("\n") if f.strip()]
        if not files:
            print("   No changed files. Nothing to scan.")
            return

    print(f"   Scanning {len(files)} file(s)...")

    # Layer 1: ESLint
    print("\n## [Layer 1] ESLint...")
    eslint = run_eslint(files)
    print(f"   Errors: {len(eslint['errors'])} | Warnings: {len(eslint['warnings'])}")
    for e in eslint["errors"][:3]:
        rel = os.path.relpath(e["file"], DEV_DIR) if os.path.isabs(e["file"]) else e["file"]
        print(f"     ERR {rel}:{e['line']}  {e['rule']} -- {e['message'][:80]}")

    # Layer 2: LLM Logic
    print("\n## [Layer 2] DeepSeek Logic Scan...")
    api_key = _get_deepseek_key()
    if not api_key:
        print("   !!  DEEPSEEK_API_KEY not found. Check .env.dev or env vars.")
        print("   Layer 2 skipped. ESLint results still available.")
        logic = []
    else:
        print(f"   API Key loaded ({api_key[:8]}...{api_key[-4:]})")
        diff = get_git_diff_text(DEV_DIR)
        if diff.strip():
            logic = analyze_logic(diff, api_key)
            if logic:
                print(f"   Found {len(logic)} potential logic issue(s):")
                for issue in logic:
                    sev = "[CRIT]" if issue.get("severity") == "critical" else "[WARN]"
                    print(f"     {sev} [{issue.get('type','?')}] {issue.get('file','?')}:{issue.get('line','?')}")
                    print(f"        {issue.get('description','')}")
            else:
                print("   No logic issues found [OK]")
        else:
            print("   No diff to analyze")
            logic = []

    # Generate report
    report = write_report(eslint, logic if api_key else [], files)
    print_report(report)

    # Final summary
    if report["has_issues"]:
        print("\n!!  Issues detected! Report ready for review.")
        print("   Type 'super-agent report' to see details, or wait for OpenClaw to notify.")
    else:
        print("\n[OK] Clean scan -- no issues found.")


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Super Agent Code Scanner v2 -- Scan only")
    parser.add_argument("--all", action="store_true", help="Scan toàn bộ project")
    parser.add_argument("--file", type=str, help="Scan 1 file cụ thể")
    args = parser.parse_args()
    scan(scan_all=args.all, single_file=args.file)


def check_report():
    """Đọc và xoá report file -- gọi từ OpenClaw session để lấy kết quả scan."""
    if os.path.isfile(REPORT_FILE):
        with open(REPORT_FILE, "r", encoding="utf-8") as f:
            report = json.load(f)
        os.unlink(REPORT_FILE)
        return report
    return None


if __name__ == "__main__":
    main()


