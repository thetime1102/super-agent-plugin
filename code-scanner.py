#!/usr/bin/env python3
"""
code-scanner.py — Super Agent Proactive Code Scanner
=====================================================
3-layer kiến trúc Event-Driven:

  Layer 1: ESLint (chuyên dụng) — quét syntax + style, auto-fix
  Layer 2: LLM (DeepSeek) — quét logic: Race Condition, Memory Leak, Missing Transaction
  Layer 3: Notify — nếu phát hiện lỗi critical → alert

Usage:
  python code-scanner.py                          # Scan từ git diff
  python code-scanner.py --all                    # Scan toàn bộ project
  python code-scanner.py --file src/service.ts    # Scan 1 file
  python code-scanner.py --notify                 # Bật notification khi có lỗi
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from typing import Optional

# ─── Paths ────────────────────────────────────────────────────────────────
WORKSPACE = r"C:\Users\tqv11\.openclaw\workspace"
DEV_DIR = os.path.join(WORKSPACE, "nhatvi-ecosystem-dev")
SUPER_AGENT_DIR = os.path.join(WORKSPACE, "super-agent-plugin")
STATE_FILE = os.path.join(WORKSPACE, "memory", ".scanner_state.json")
CONSOLIDATION_DB = os.path.join(WORKSPACE, "memory", "consolidation.db")

# ─── State Tracking ───────────────────────────────────────────────────────

def get_last_scan_sha() -> str:
    try:
        with open(STATE_FILE) as f:
            return json.load(f).get("last_sha", "")
    except Exception:
        return ""

def set_last_scan_sha(sha: str):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"last_sha": sha, "updated_at": time.time()}, f)


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 1: ESLint — Syntax & Style Scanner
# ═══════════════════════════════════════════════════════════════════════════

def run_eslint(files: list[str]) -> dict:
    """
    Chạy ESLint với --format json, trả về parsed results.
    """
    if not files:
        return {"errors": [], "warnings": [], "auto_fixable": []}

    results = {"errors": [], "warnings": [], "auto_fixable": []}

    try:
        cmd = ["npx.cmd", "eslint", "--format", "json", "--max-warnings", "999"] + files
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=60, cwd=DEV_DIR,
            encoding="utf-8", errors="replace",
        )

        if proc.returncode not in (0, 1):  # 0=clean, 1=has warnings/errors
            print(f"   [eslint] Process error: {proc.stderr[:200]}")
            return results

        eslint_out = proc.stdout.strip()
        if not eslint_out:
            return results

        parsed = json.loads(eslint_out)
        for file_result in parsed:
            file_path = file_result.get("filePath", "")
            for msg in file_result.get("messages", []):
                entry = {
                    "file": file_path,
                    "line": msg.get("line", 0),
                    "col": msg.get("column", 0),
                    "end_line": msg.get("endLine", msg.get("line", 0)),
                    "end_col": msg.get("endColumn", msg.get("column", 0)),
                    "rule": msg.get("ruleId", "unknown"),
                    "message": msg.get("message", ""),
                    "severity": msg.get("severity", 2),  # 1=warn, 2=error
                    "fix": msg.get("fix"),  # ESLint fix info
                }

                if entry["severity"] == 2:
                    results["errors"].append(entry)
                else:
                    results["warnings"].append(entry)

                if entry["fix"]:
                    results["auto_fixable"].append(entry)

    except FileNotFoundError:
        print("   [eslint] ESLint not found. Run: npm install")
    except subprocess.TimeoutExpired:
        print("   [eslint] Timeout (60s)")
    except json.JSONDecodeError as e:
        print(f"   [eslint] JSON parse error: {e}")
    except Exception as e:
        print(f"   [eslint] Error: {e}")

    return results


def auto_fix_eslint(results: dict) -> int:
    """
    Auto-fix ESLint errors using replace_code_symbol.
    Returns number of fixes applied.
    """
    fixes = 0
    replace_tool = os.path.join(SUPER_AGENT_DIR, "replace_code_symbol.py")

    if not os.path.isfile(replace_tool):
        print("   [fix] replace_code_symbol.py not found")
        return 0

    for entry in results.get("auto_fixable", []):
        fix = entry.get("fix")
        if not fix:
            continue

        file_path = entry["file"]
        if not os.path.isfile(file_path):
            # ESLint reports absolute path, but we might need to construct it
            alt = os.path.join(DEV_DIR, os.path.basename(file_path))
            if os.path.isfile(alt):
                file_path = alt
            else:
                continue

        # Read file
        try:
            with open(file_path, "rb") as f:
                source = f.read()

            text_start = fix.get("text")
            if not text_start:
                continue

            range_start = fix.get("range", [entry["col"] - 1, entry["col"]])
            if len(range_start) < 2:
                continue

            # Simple byte-range replace as suggested by ESLint fix
            start_byte = range_start[0]
            end_byte = range_start[1]
            new_bytes = source[:start_byte] + text_start.encode("utf-8") + source[end_byte:]

            # Backup + write
            backup = file_path + ".scanbak"
            import shutil
            shutil.copy2(file_path, backup)

            with open(file_path, "wb") as f:
                f.write(new_bytes)

            fixes += 1
            os.unlink(backup)  # Clean up backup after successful write

        except Exception as e:
            print(f"   [fix] Failed: {entry['rule']} in {os.path.basename(file_path)}: {e}")

    return fixes


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 2: LLM Logic Scan — DeepSeek-powered
# ═══════════════════════════════════════════════════════════════════════════

def get_git_diff_text(git_dir: str, since_sha: str = "") -> str:
    """Get full diff text for LLM analysis."""
    if not os.path.isdir(os.path.join(git_dir, ".git")):
        return ""

    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, timeout=15, cwd=git_dir)
        current = r.stdout.decode("utf-8", errors="replace").strip()
        if not current:
            return ""

        base = since_sha if since_sha else f"{current}~1"

        r = subprocess.run(
            ["git", "diff", f"{base}..{current}", "--", "*.ts", "*.tsx", "*.js", "*.py"],
            capture_output=True, timeout=30, cwd=git_dir,
        )
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def call_deepseek(prompt: str) -> str:
    """Call DeepSeek API for logic analysis. Uses project's DeepSeek config."""
    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        # Try loading from .env
        env_file = os.path.join(DEV_DIR, ".env.dev")
        if os.path.isfile(env_file):
            with open(env_file) as f:
                for line in f:
                    if "DEEPSEEK_API_KEY" in line:
                        api_key = line.split("=", 1)[1].strip().strip("'\"")
                        break

    if not api_key:
        return "⚠️ DeepSeek API key not configured"

    import urllib.request
    import urllib.error

    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": "You are a senior TypeScript code reviewer. Analyze diffs for logic bugs only. Be concise."},
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
    except Exception as e:
        return f"⚠️ LLM call failed: {e}"


def analyze_logic(diff_text: str) -> list[dict]:
    """
    LLM phân tích git diff tìm lỗi logic:
    - Race Condition (concurrent DB writes)
    - Memory Leak (missing cleanup in React/Vue)
    - Missing Transaction (multi-step DB without transaction)
    """
    if not diff_text.strip():
        return []

    prompt = f"""Analyze this git diff for logic bugs ONLY (not syntax/style):

1. RACE CONDITION: Concurrent DB writes without locking? Shared state mutation?
2. MEMORY LEAK: setInterval/setTimeout/addEventListener without cleanup? React useEffect missing return cleanup?
3. MISSING TRANSACTION: Two+ sequential DB writes without BEGIN/COMMIT or transaction wrapper?
4. ASYNC SAFETY: Promise.all on dependent operations? Missing await in critical path?

DIFF:
```
{diff_text[:8000]}
```

Respond in JSON format ONLY:
```json
[
  {{
    "type": "race_condition|memory_leak|missing_transaction|async_safety",
    "severity": "critical|warning",
    "file": "filename.ts",
    "line": 42,
    "description": "Brief description",
    "suggestion": "How to fix"
  }}
]
```
If no issues found, respond with: []"""

    response = call_deepseek(prompt)
    if response.startswith("⚠️"):
        return [{"type": "error", "severity": "warning", "description": response}]

    # Extract JSON array from response
    try:
        import re as regex
        json_match = regex.search(r'\[\s*\{.*\}\s*\]', response, regex.DOTALL)
        if json_match:
            return json.loads(json_match.group())
        return []
    except Exception:
        return [{"type": "parse_error", "severity": "warning", "description": response[:200]}]


# ═══════════════════════════════════════════════════════════════════════════
# LAYER 3: Notification
# ═══════════════════════════════════════════════════════════════════════════

def format_alert(logic_issues: list[dict]) -> str:
    """Format logic issues for display/notification."""
    if not logic_issues:
        return ""

    criticals = [i for i in logic_issues if i.get("severity") == "critical"]
    warnings = [i for i in logic_issues if i.get("severity") != "critical"]

    lines = []
    if criticals:
        lines.append("🚨 **CRITICAL LOGIC BUGS DETECTED** 🚨")
        lines.append("")
        for issue in criticals[:5]:
            lines.append(f"  🔴 [{issue['type']}] {issue.get('file', '?')}:{issue.get('line', '?')}")
            lines.append(f"     {issue.get('description', '')}")
            lines.append(f"     → Fix: {issue.get('suggestion', '')}")
            lines.append("")

    if warnings:
        if criticals:
            lines.append("---")
        lines.append("⚠️  Warnings:")
        for issue in warnings[:3]:
            lines.append(f"  🟡 [{issue['type']}] {issue.get('description', '')}")

    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# MAIN SCANNER
# ═══════════════════════════════════════════════════════════════════════════

def scan(notify: bool = False, scan_all: bool = False, single_file: str = ""):
    """Main scan pipeline: ESLint → LLM Logic → Notify."""
    print("** Code Scanner starting...")

    # ── Step 0: Determine files to scan ──
    files_to_scan = []

    if single_file:
        files_to_scan = [single_file]
    elif scan_all:
        files_to_scan = ["src/", "web/src/"]
    else:
        # Scan git diff (last commit)
        last_sha = get_last_scan_sha()
        diff_text = get_git_diff_text(DEV_DIR, last_sha)

        if not diff_text.strip():
            print("   No new changes to scan")
            # Still run ESLint on recent files
            r = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1..HEAD", "--", "*.ts", "*.tsx"],
                capture_output=True, timeout=15, cwd=DEV_DIR,
            )
            files = r.stdout.decode("utf-8", errors="replace").strip().split("\n")
            files_to_scan = [f for f in files if f.strip()]
            if not files_to_scan:
                print("   No files changed. Exiting.")
                return
        else:
            # Extract files from diff
            for line in diff_text.split("\n"):
                if line.startswith("+++ b/"):
                    f = line[6:]
                    if f.endswith((".ts", ".tsx", ".js", ".py")):
                        files_to_scan.append(f)

    if not files_to_scan:
        files_to_scan = ["src/", "web/src/"]

    print(f"   Scanning: {len(files_to_scan)} file(s)")

    # ── Layer 1: ESLint ──
    print("\n** [Layer 1] ESLint Scan...")
    eslint_results = run_eslint(files_to_scan)

    if eslint_results["errors"] or eslint_results["warnings"]:
        print(f"   ESLint: {len(eslint_results['errors'])} errors, {len(eslint_results['warnings'])} warnings")
        if eslint_results["auto_fixable"]:
            print(f"   Auto-fixable: {len(eslint_results['auto_fixable'])} issues")
            fixes = auto_fix_eslint(eslint_results)
            print(f"   Auto-fixed: {fixes} issues")
    else:
        print("   ESLint: clean ✅")

    # ── Layer 2: LLM Logic Scan (chỉ khi có diff) ──
    logic_issues = []
    print("\n** [Layer 2] LLM Logic Scan...")

    if not single_file and not scan_all:
        diff_text = get_git_diff_text(DEV_DIR, get_last_scan_sha())
        if diff_text.strip():
            logic_issues = analyze_logic(diff_text)
            if logic_issues:
                print(f"   LLM found: {len(logic_issues)} potential issues")
            else:
                print("   LLM: clean ✅")
        else:
            print("   No diff to analyze")
    else:
        print("   Skipped (--file or --all mode)")

    # ── Report ──
    print("\n** Summary:")
    print(f"   ESLint: {len(eslint_results['errors'])} err / {len(eslint_results['warnings'])} warn")
    print(f"   Logic: {len(logic_issues)} issue(s)")

    # Update tracking SHA
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, timeout=15, cwd=DEV_DIR)
        set_last_scan_sha(r.stdout.decode("utf-8", errors="replace").strip())
    except Exception:
        pass

    # Alert if critical
    criticals = [i for i in logic_issues if i.get("severity") == "critical"]
    if criticals:
        msg = format_alert(logic_issues)
        print(f"\n{msg}")
        print("\n⚠️  Critical logic issues detected! Use replace_code_symbol --dry-run to review.")
    elif logic_issues:
        msg = format_alert(logic_issues)
        print(f"\n{msg}")

    return logic_issues, eslint_results


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Super Agent Code Scanner - ESLint + LLM Logic")
    parser.add_argument("--all", action="store_true", help="Scan toàn bộ project")
    parser.add_argument("--file", type=str, help="Scan 1 file cụ thể")
    parser.add_argument("--notify", action="store_true", help="Bật notification khi có lỗi")
    args = parser.parse_args()

    scan(notify=args.notify, scan_all=args.all, single_file=args.file)


if __name__ == "__main__":
    main()
