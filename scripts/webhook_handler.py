#!/usr/bin/env python3
"""
webhook_handler.py — CI/CD Auto-Fix (Phase 7, Production-Ready)
=================================================================
Lắng nghe GitHub Workflow webhooks → tự động chạy luồng Đa đặc vụ khi CI fail.

Bảo vệ sản xuất (Production Guards):
  1. GIT WORKTREE (tránh race condition) — mỗi run_id làm việc trên worktree riêng
  2. INFINITE LOOP GUARD — bỏ qua auto-fix/ branches (không tự trigger lại)
  3. VERIFIED_ONLY_ON_CI_PASS — PatternStore chỉ ghi khi CI pass thật sự
  4. SMART ERROR EXTRACTION — Regex tìm Error/Failed thay vì blind tail truncation

Flow hoàn chỉnh:
  [GitHub Actions FAIL] -- webhook POST --> [Cloudflare Tunnel]
       --> [Gateway] --> [webhook_handler.py]
            1. parse_workflow_run() -> ignore auto-fix branches
            2. fetch logs + smart_extract_error()
            3. run_orchestrator(bug_report)
            4. Nếu APPROVED:
               - git worktree add <temp-dir>
               - git checkout -b auto-fix/run-<run_id> (trong worktree)
               - git add + git commit
               - git push origin auto-fix/run-<run_id>
               - gh pr create
               - git worktree remove (dọn dẹp)

Yêu cầu môi trường:
  - GitHub CLI (gh): https://cli.github.com/
  - `gh auth login` hoặc GITHUB_TOKEN env var
  - GITHUB_TOKEN:  Personal Access Token voi quyen repo + workflow
  - Python 3.10+ voi requests, multi_agent_orchestrator

Usage:
  python webhook_handler.py --daemon --port 11999 --secret "webhook-secret"
  python webhook_handler.py --health
  python webhook_handler.py --test-event workflow_run --test-conclusion failure
  python webhook_handler.py --fetch-logs thetime1102/nhatvi-ecosystem-dev 12345
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import textwrap
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

# ─── Windows cp932 fix ──────────────────────────────────────────────────
# Disabled — causes ValueError in subprocess-nested python on Python 3.14.
# Set PYTHONIOENCODING=utf-8 in environment instead.

# ─── Paths ────────────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ─── Platform-aware paths (Windows dev vs Linux prod) ──────────────────
if sys.platform == "win32":
    _WORKSPACE = r"C:\Users\tqv11\.openclaw\workspace"
    _DEV_DIR = os.path.join(_WORKSPACE, "nhatvi-ecosystem-dev")
    _WORKTREE_DIR = os.path.join(_WORKSPACE, "auto-fix-worktrees")
    _PENDING_DB_DIR = os.path.join(_WORKSPACE, "memory")
else:
    # Linux (VM2 Production)
    _WORKSPACE = os.path.expanduser("~/super-agent")
    _DEV_DIR = os.path.expanduser("~/nhatvicake-core")
    _WORKTREE_DIR = os.path.join(_WORKSPACE, "auto-fix-worktrees")
    _PENDING_DB_DIR = os.path.join(_WORKSPACE, "memory")

_SUPER_AGENT_DIR = os.path.join(_WORKSPACE, "super-agent-plugin")
sys.path.insert(0, _SUPER_AGENT_DIR)

# ─── Optional: PatternStore — chi record khi CI PASS that su ───────────
try:
    from pattern_store import PatternStore
    _pattern_store = PatternStore()
except ImportError:
    _pattern_store = None
    print("[WARN] pattern_store not available", file=sys.stderr)

# ─── Optional: Multi-Agent Orchestrator ─────────────────────────────────
try:
    import multi_agent_orchestrator
    HAS_ORCHESTRATOR = True
except ImportError as e:
    HAS_ORCHESTRATOR = False
    print(f"[WARN] multi_agent_orchestrator not available: {e}", file=sys.stderr)


# ═══════════════════════════════════════════════════════════════════════════
#  Constants
# ═══════════════════════════════════════════════════════════════════════════

_GIT_REMOTE = os.environ.get("GIT_REMOTE", "origin")
_GIT_BASE_BRANCH = os.environ.get("GIT_BASE_BRANCH", "dev")
_AUTO_FIX_REMOTE = _GIT_REMOTE
_LOG_ENCODING = os.environ.get("LOG_ENCODING", "utf-8")
_MAX_LOG_CHARS = int(os.environ.get("MAX_LOG_CHARS", "3000"))
_PROJECT_DIR = _DEV_DIR
_DEFAULT_REPO = os.environ.get("GITHUB_REPOSITORY", "thetime1102/nhatvicake-core")

# Danh sách nhánh auto-fix bi co gioi han (infinite loop guard)
_AUTO_FIX_PREFIX = "auto-fix/"
# SQLite DB luu pending fixes (context persistence giua cac webhook)
_PENDING_DB = os.path.join(_PENDING_DB_DIR, "pending_fixes.db")


# ═══════════════════════════════════════════════════════════════════════════
#  Auto-Setup: ghep PATH + set GH_TOKEN tu dong (khong can env thu cong)
# ═══════════════════════════════════════════════════════════════════════════

def _auto_setup_env():
    """
    Tu dong tim gh CLI va set GH_TOKEN khi import module.
    - Them `gh.exe` vao PATH neu chua co
    - Set bien moi truong GH_TOKEN tu _get_github_token()
    """
    # --- Them gh CLI vao PATH neu chua co ---
    gh_dirs = [
        r"C:\Program Files\GitHub CLI",
        r"C:\Program Files (x86)\GitHub CLI",
        os.path.expanduser(r"~\AppData\Local\GitHub CLI"),
        os.path.expanduser(r"~\scoop\shims"),
    ]
    found_gh = False
    for d in gh_dirs:
        gh_exe = os.path.join(d, "gh.exe")
        if os.path.isfile(gh_exe):
            if d not in os.environ.get("PATH", ""):
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
            found_gh = True
            break

    # Thu tim bang where.exe neu chua tim thay
    if not found_gh:
        try:
            r = subprocess.run(["where.exe", "gh"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip():
                gh_path = r.stdout.strip().splitlines()[0].strip()
                gh_dir = os.path.dirname(gh_path)
                if gh_dir not in os.environ.get("PATH", ""):
                    os.environ["PATH"] = gh_dir + os.pathsep + os.environ.get("PATH", "")
                found_gh = True
        except Exception:
            pass

    if not found_gh:
        print("[AutoSetup] gh CLI not found. Install: winget install GitHub.cli", flush=True)

    # --- Tu dong set GH_TOKEN ---
    token = (
        os.environ.get("GITHUB_TOKEN", "")
        or os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
        or os.environ.get("GH_TOKEN", "")
    )
    if token:
        os.environ.setdefault("GH_TOKEN", token)
        os.environ.setdefault("GITHUB_TOKEN", token)
        print(f"[AutoSetup] GH_TOKEN auto-set: {token[:8]}...", flush=True)
    else:
        print("[AutoSetup] No GitHub token found (check GITHUB_PERSONAL_ACCESS_TOKEN)", flush=True)


# Chay auto-setup ngay khi module duoc import
_auto_setup_env()


# ═══════════════════════════════════════════════════════════════════════════
#  Error Pattern Regex (Fix #4: Smart Error Extraction)
# ═══════════════════════════════════════════════════════════════════════════

# Cac pattern tim loi chinh xac hon blind tail truncation
_ERROR_TRIGGER_PATTERNS = re.compile(
    r"(?:"
    r"Error\s*:|Error\b|ERROR\b|"
    r"Exception\b|exception\b|"
    r"Failed at|FAILED|failed\b|"
    r"Traceback|traceback|Stack trace|stack trace|"
    r"Cannot find|cannot find|not found|NOT FOUND|"
    r"Exit code 1|exit code 1|process exited with code 1|"
    r"SyntaxError|TypeError|ReferenceError|RangeError|"
    r"ECONNREFUSED|ECONNRESET|ETIMEDOUT|ENOENT|EACCES|"
    r"Timeout|timeout|Time out|"
    r"✗|×|FAIL|"
    r"Module not found|cannot resolve|"
    r"ERR_PNPM|ERR_NPM|ERR_YARN|"
    r"Segmentation fault|segfault|"
    r"Killed|OOM|out of memory"
    r")",
    re.IGNORECASE,
)

# So dong context truoc/sau dong loi de lay
_ERROR_CONTEXT_LINES = 30


# ═══════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════

def _log(msg: str, level: str = "INFO") -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{level}] {ts}  {msg}", flush=True)


def _run_git(cmd: list[str], cwd: str = _PROJECT_DIR, capture: bool = True) -> subprocess.CompletedProcess:
    full_cmd = ["git"] + cmd
    _log(f"git {' '.join(shlex.quote(c) for c in cmd)}", "CMD")
    result = subprocess.run(
        full_cmd, cwd=cwd, capture_output=capture, text=True,
        encoding=_LOG_ENCODING, errors="replace", timeout=60,
    )
    if result.returncode != 0:
        err = result.stderr[:500] if result.stderr else "(no stderr)"
        _log(f"git stderr: {err}", "WARN")
    return result


def _run_gh(cmd: list[str], cwd: str = _PROJECT_DIR) -> subprocess.CompletedProcess:
    full_cmd = ["gh"] + cmd
    _log(f"gh {' '.join(shlex.join(c) for c in cmd)}", "CMD")
    result = subprocess.run(
        full_cmd, cwd=cwd, capture_output=True, text=True,
        encoding=_LOG_ENCODING, errors="replace", timeout=120,
    )
    if result.returncode != 0:
        _log(f"gh stderr: {result.stderr[:500]}", "WARN")
    return result


def _check_gh_installed() -> bool:
    try:
        r = subprocess.run(["gh", "--version"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _check_git_installed() -> bool:
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=10)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  Telegram Alert (ChatOps)
# ═══════════════════════════════════════════════════════════════════════════

def send_telegram_alert(message: str) -> None:
    """
    Send a monitoring alert to Telegram via Bot API.

    Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from environment.
    Silently returns if either is missing (safe to call on any server).
    Uses only Python stdlib (urllib) — no third-party dependencies.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        _log("Telegram alert skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set", "DEBUG")
        return

    try:
        import urllib.request
        import urllib.parse

        text = f"🤖 *Auto-Fix CI/CD*\n\n{message}"
        params = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": "true",
        })
        url = f"https://api.telegram.org/bot{token}/sendMessage?{params}"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                _log(f"Telegram API returned HTTP {resp.status}", "WARN")
    except urllib.error.URLError as e:
        _log(f"Telegram network error: {e.reason}", "WARN")
    except ImportError:
        _log("urllib not available for Telegram alert", "WARN")
    except Exception as e:
        _log(f"Telegram alert failed: {e}", "WARN")


# ═══════════════════════════════════════════════════════════════════════════
#  A. Pending Fix Database (Fix #5: context persistence)
# ═══════════════════════════════════════════════════════════════════════════

_PENDING_DB_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_fixes (
    run_id INTEGER PRIMARY KEY,
    repo TEXT NOT NULL,
    branch TEXT NOT NULL,
    workflow TEXT NOT NULL DEFAULT '',
    html_url TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','pr_created','ci_passed','failed')),
    pr_url TEXT NOT NULL DEFAULT '',
    fix_plan TEXT NOT NULL DEFAULT '{}',
    qa_feedback TEXT NOT NULL DEFAULT '',
    strategy TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_pf_branch ON pending_fixes(branch);
CREATE INDEX IF NOT EXISTS idx_pf_status ON pending_fixes(status);
"""


def _init_pending_db() -> sqlite3.Connection:
    """Init pending_fixes DB, return connection."""
    os.makedirs(os.path.dirname(_PENDING_DB), exist_ok=True)
    conn = sqlite3.connect(_PENDING_DB, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_PENDING_DB_SCHEMA)
    conn.commit()
    return conn


def _save_pending_fix(
    run_id: int,
    repo: str,
    branch: str,
    workflow: str,
    html_url: str,
    fix_plan: dict,
    qa_feedback: str,
) -> None:
    """Luu pending fix vao DB truoc khi tao PR (context persistence)."""
    strategy = fix_plan.get("strategy", "")
    files = ", ".join(fix_plan.get("files", []))

    conn = _init_pending_db()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO pending_fixes
               (run_id, repo, branch, workflow, html_url, status,
                fix_plan, qa_feedback, strategy, files, updated_at)
               VALUES (?, ?, ?, ?, ?, 'pending',
                       ?, ?, ?, ?, datetime('now'))""",
            (
                run_id, repo, branch, workflow, html_url,
                json.dumps(fix_plan, ensure_ascii=False),
                qa_feedback, strategy, files,
            ),
        )
        conn.commit()
        _log(f"Saved pending fix for run #{run_id} ({strategy})")
    except Exception as e:
        _log(f"Failed to save pending fix: {e}", "WARN")
    finally:
        conn.close()


def _lookup_pending_fix(branch: str) -> Optional[dict]:
    """Tra cuu pending fix theo branch name.
    Tra ve dict neu co, None neu khong."""
    conn = _init_pending_db()
    try:
        row = conn.execute(
            "SELECT * FROM pending_fixes WHERE branch = ?", (branch,)
        ).fetchone()
        if row:
            result = dict(row)
            # Parse JSON fix_plan
            try:
                result["fix_plan"] = json.loads(result.get("fix_plan", "{}"))
            except (json.JSONDecodeError, TypeError):
                result["fix_plan"] = {}
            return result
        return None
    except Exception as e:
        _log(f"Failed to lookup pending fix: {e}", "WARN")
        return None
    finally:
        conn.close()


def _update_pending_fix_status(branch: str, status: str, pr_url: str = "") -> None:
    """Cap nhat status cua pending fix."""
    conn = _init_pending_db()
    try:
        conn.execute(
            """UPDATE pending_fixes
               SET status = ?, pr_url = ?, updated_at = datetime('now')
               WHERE branch = ?""",
            (status, pr_url, branch),
        )
        conn.commit()
        _log(f"Updated pending fix {branch}: status={status}")
    except Exception as e:
        _log(f"Failed to update pending fix: {e}", "WARN")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════════════
#  B. Stale Worktree Cleanup (Fix #5: startup cleanup)
# ═══════════════════════════════════════════════════════════════════════════

def _cleanup_stale_worktrees() -> int:
    """
    Don dep cac git worktree con sot lai tu lan chay truoc.
    Xoa worktree trong _WORKTREE_DIR khong con trong `git worktree list`,
    va xoa worktree `git worktree remove --force` neu con.

    Returns:
        So luong worktree da don dep.
    """
    if not os.path.isdir(_WORKTREE_DIR):
        return 0

    cleaned = 0
    _log("Cleaning up stale worktrees...")

    # Liệt kê worktree đang được git quản lý
    try:
        r = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=_PROJECT_DIR,
            capture_output=True, text=True, encoding=_LOG_ENCODING,
            timeout=30,
        )
        active_worktrees = set()
        for line in r.stdout.splitlines():
            if line.startswith("worktree "):
                active_worktrees.add(os.path.normpath(line[9:]))
    except Exception:
        active_worktrees = set()
        _log("Could not list git worktrees", "WARN")

    # Quét thư mục worktree base, xoá những cái stale
    for entry in os.listdir(_WORKTREE_DIR):
        wt_path = os.path.normpath(os.path.join(_WORKTREE_DIR, entry))
        if not os.path.isdir(wt_path):
            continue
        if not os.path.isdir(os.path.join(wt_path, ".git")):
            # Không phải git repo -> xoá thư mục rác
            try:
                shutil.rmtree(wt_path, ignore_errors=True)
                cleaned += 1
                _log(f"Removed non-git directory: {entry}")
            except Exception as e:
                _log(f"Failed to remove {entry}: {e}", "WARN")
            continue
        if wt_path not in active_worktrees:
            # Worktree đã detached -> `git worktree remove --force`
            try:
                subprocess.run(
                    ["git", "worktree", "remove", "--force", wt_path],
                    cwd=_PROJECT_DIR,
                    capture_output=True, timeout=30,
                )
                shutil.rmtree(wt_path, ignore_errors=True)
                cleaned += 1
                _log(f"Removed stale worktree: {entry}")
            except Exception as e:
                _log(f"Failed to remove worktree {entry}: {e}", "WARN")
                # Force delete directory anyway
                try:
                    shutil.rmtree(wt_path, ignore_errors=True)
                    cleaned += 1
                except Exception:
                    pass

    if cleaned:
        _log(f"Cleaned {cleaned} stale worktree(s)")
    else:
        _log("No stale worktrees found")
    return cleaned


# ═══════════════════════════════════════════════════════════════════════════
#  1. Signature Verification
# ═══════════════════════════════════════════════════════════════════════════

def verify_signature(payload_body: bytes, signature_header: str, secret: str) -> bool:
    if not secret:
        return True
    if not signature_header:
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), payload_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)


# ═══════════════════════════════════════════════════════════════════════════
#  2. Webhook Payload Parsing + Auto-Fix Branch Guard (Fix #2)
# ═══════════════════════════════════════════════════════════════════════════

def parse_workflow_run(payload: dict) -> Optional[dict]:
    """
    Parse GitHub 'workflow_run' event.

    Guards (Fix #2 — Infinite Loop):
      - Only process 'completed' action
      - Ignore auto-fix/ branches (ngan vong lap CI trigger)
      - Ignore cancelled conclusions
    """
    if payload.get("action") not in ("completed", "requested"):
        return None

    workflow_run = payload.get("workflow_run", {})
    conclusion = workflow_run.get("conclusion", "")
    head_branch = workflow_run.get("head_branch", "")
    head_sha = workflow_run.get("head_sha", "")
    head_repo = workflow_run.get("head_repository", {}).get("full_name", "")
    workflow_name = workflow_run.get("name", "")
    html_url = workflow_run.get("html_url", "")
    run_id = workflow_run.get("id", 0)
    logs_url = workflow_run.get("logs_url", "")

    # ─── Fix #2: Infinite Loop Guard ─────────────────────────────────
    # Bo qua cac su kien tren nhanh auto-fix/ de tranh CI trigger loop:
    #   CI fail -> Webhook -> create_auto_fix_pr -> CI chay lai
    #   -> Webhook khac -> create_auto_fix_pr [...] (loop infinite)
    if head_branch.lower().startswith(_AUTO_FIX_PREFIX):
        _log(
            f"Ignoring event on auto-fix branch '{head_branch}' "
            f"(infinite loop guard) — run #{run_id} conclusion={conclusion}",
            "WARN",
        )
        return None

    return {
        "event": "workflow_run",
        "action": payload.get("action"),
        "conclusion": conclusion,
        "branch": head_branch,
        "sha": head_sha,
        "repo": head_repo or _DEFAULT_REPO,
        "workflow": workflow_name,
        "run_id": run_id,
        "html_url": html_url,
        "logs_url": logs_url,
    }


# ═══════════════════════════════════════════════════════════════════════════
#  3. Smart Error Extraction (Fix #4 — thay the blind tail truncation)
# ═══════════════════════════════════════════════════════════════════════════

def _extract_error_context(raw_logs: str, max_chars: int = _MAX_LOG_CHARS) -> str:
    """
    Trích xuất context loi thong minh tu raw logs bang regex.

    Strategy:
      1. Scan tung dong tim _ERROR_TRIGGER_PATTERNS
      2. Voi moi match, lay ERROR_CONTEXT_LINES dong xung quanh
      3. Gop cac vung overlap lai, them .....` delimiter`
      4. Neu khong match pattern nao -> fallback ve tail truncation
      5. Gioi han max_chars ky tu cuoi cung

    Args:
        raw_logs: Full log output tu gh CLI
        max_chars: So ky tu toi da cho bug_report

    Returns:
        String da duoc trich loc, san cho orchestrator.
    """
    lines = raw_logs.splitlines()
    if not lines:
        return "[EMPTY] No log lines."

    # ── B1: Tim cac dong loi ──────────────────────────────────────────
    error_indices: set[int] = set()
    match_count = 0

    for i, line in enumerate(lines):
        if _ERROR_TRIGGER_PATTERNS.search(line):
            error_indices.add(i)
            match_count += 1

    _log(f"Smart extraction: found {match_count} error trigger(s) in {len(lines)} lines")

    # ── B2: Mo rong context xung quanh moi dong loi ──────────────────
    if error_indices:
        # Lay toan bo block indices (gom overlap)
        context_indices: set[int] = set()
        for idx in sorted(error_indices):
            start = max(0, idx - _ERROR_CONTEXT_LINES)
            end = min(len(lines), idx + _ERROR_CONTEXT_LINES + 1)
            # Ghi nhan khoang
            for j in range(start, end):
                context_indices.add(j)

        # Gop cac block lien tuc
        sorted_indices = sorted(context_indices)
        blocks: list[list[int]] = []
        current_block: list[int] = [sorted_indices[0]]

        for idx in sorted_indices[1:]:
            if idx == current_block[-1] + 1:
                current_block.append(idx)
            else:
                blocks.append(current_block)
                current_block = [idx]
        if current_block:
            blocks.append(current_block)

        # Xay dung output tu cac block
        output_parts: list[str] = []
        for block_idx, block in enumerate(blocks):
            if block_idx > 0:
                output_parts.append(f"..... ({block[0] - blocks[block_idx-1][-1] - 1} lines omitted)")
            for line_idx in block:
                output_parts.append(lines[line_idx])

        combined = "\n".join(output_parts)

        # ── B3: Gioi han max_chars, uu tien duoi (stack trace quan trong) ──
        if len(combined) > max_chars:
            _log(f"Smart extraction truncated from {len(combined)} to {max_chars} chars", "INFO")
            combined = combined[-max_chars:]
            first_nl = combined.find("\n")
            if first_nl > 0:
                combined = "[...truncated...]\n" + combined[first_nl + 1:]

        return combined

    # ── B4: Fallback — khong tim thay error pattern, dung tail ──────
    _log("No error triggers found — falling back to tail truncation", "WARN")
    combined = "\n".join(lines)
    if len(combined) > max_chars:
        combined = combined[-max_chars:]
        first_nl = combined.find("\n")
        if first_nl > 0:
            combined = "[...tail (no error triggers found)...]\n" + combined[first_nl + 1:]

    return combined


# ═══════════════════════════════════════════════════════════════════════════
#  4. Fetch Error Logs tu GitHub Actions
# ═══════════════════════════════════════════════════════════════════════════

def fetch_github_action_logs(repo: str, run_id: int, max_chars: int = _MAX_LOG_CHARS) -> str:
    """
    Fetch error logs from GitHub Actions via `gh run view --log-failed`.

    Args:
        repo: "owner/repo"
        run_id: GitHub Actions Run ID
        max_chars: So ky tu toi da cho smart extraction

    Returns:
        String bug_report da duoc smart-extract, san cho orchestrator.

    Raises:
        RuntimeError: Neu gh CLI khong co san hoac khong auth duoc.
    """
    if not _check_gh_installed():
        raise RuntimeError(
            "GitHub CLI (gh) not installed — install from https://cli.github.com/ "
            "and run 'gh auth login'"
        )

    _log(f"Fetching failed logs for {repo} run #{run_id}...")

    # ── Primary: `gh run view <run_id> --log-failed` ─────────────────
    try:
        result = _run_gh(["run", "view", str(run_id), "--log-failed", "--repo", repo])
    except subprocess.TimeoutExpired:
        _log("gh run view --log-failed timed out (120s)", "WARN")
        return "[TIMEOUT] gh command timed out after 120s"

    if result.returncode != 0:
        stderr = result.stderr.strip()
        _log(f"gh run view --log-failed failed: {stderr[:300]}", "WARN")

        # ── Fallback: `gh run view <run_id> --log` (full logs) ─────
        _log("Fallback: fetching ALL logs...")
        try:
            result = _run_gh(["run", "view", str(run_id), "--log", "--repo", repo])
        except subprocess.TimeoutExpired:
            _log("gh run view --log timed out", "WARN")
            return "[TIMEOUT] gh command timed out"

        if result.returncode != 0:
            raise RuntimeError(
                f"Failed to fetch logs for run #{run_id}. "
                f"gh exited with code {result.returncode}: {stderr[:300]}"
            )

    raw_logs = result.stdout

    if not raw_logs.strip():
        _log("Log output is empty — run may still be in progress or logs expired", "WARN")
        return "[EMPTY] No log output available for this run."

    # ── Smart Extraction (Fix #4) — thay the blind tail truncation ──
    _log(f"Raw log size: {len(raw_logs)} chars")
    bug_report = _extract_error_context(raw_logs, max_chars=max_chars)
    _log(f"Smart-extracted bug report: {len(bug_report)} chars")

    # ── Fix #5: Hard cap — dam bao output cuoi cung khong tran MAX_LOG_CHARS ──
    if len(bug_report) > max_chars:
        _log(f"Hard cap: truncating from {len(bug_report)} to {max_chars} chars", "INFO")
        bug_report = bug_report[-max_chars:]
        first_nl = bug_report.find("\n")
        if first_nl > 0:
            bug_report = "[...hard cap...]\n" + bug_report[first_nl + 1:]

    return bug_report


# ═══════════════════════════════════════════════════════════════════════════
#  5. Trigger Multi-Agent Orchestrator
# ═══════════════════════════════════════════════════════════════════════════

def _call_orchestrator(bug_report: str) -> dict:
    """Goi multi_agent_orchestrator.run_orchestrator() va tra ve ket qua."""
    if not HAS_ORCHESTRATOR:
        raise RuntimeError(
            "multi_agent_orchestrator not available in Python path"
        )

    _log("Calling multi_agent_orchestrator.run_orchestrator()...")
    _log(f"Bug report length: {len(bug_report)} chars")

    try:
        result = multi_agent_orchestrator.run_orchestrator(bug_report)
        _log(f"Orchestrator returned: success={result.get('success')}, "
             f"iterations={result.get('iterations')}")
        return result
    except RuntimeError:
        raise
    except Exception as e:
        _log(f"Orchestrator crashed: {e}", "ERROR")
        _log(traceback.format_exc(), "ERROR")
        raise RuntimeError(f"Orchestrator unexpected error: {e}")


# ═══════════════════════════════════════════════════════════════════════════
#  6. Create Auto-Fix PR — voi Git Worktree (Fix #1)
# ═══════════════════════════════════════════════════════════════════════════

def create_auto_fix_pr(
    repo: str,
    run_id: int,
    branch_name: str,
    fix_plan: dict,
    qa_feedback: str,
) -> dict:
    """
    Tao auto-fix PR dung git worktree de tranh race condition.

    Fix #1 (Git Concurrency):
      - Thay vi thao tac truc tiep tren _PROJECT_DIR, dung git worktree
      - Moi run_id co worktree rieng: <WORKTREE_DIR>/run-<run_id>/
      - Xoa worktree sau khi hoan tat (try/finally dam bao cleanup)

    Args:
        repo: "owner/repo"
        run_id: GitHub Actions Run ID
        branch_name: "auto-fix/run-<run_id>"
        fix_plan: Dict tu orchestrator {strategy, summary, files}
        qa_feedback: String feedback tu ReviewerAgent

    Returns:
        Dict {pr_url, branch, commit_sha, status}
    """
    strategy = fix_plan.get("strategy", "UNKNOWN")
    summary = fix_plan.get("summary", "Auto-fix")
    files = fix_plan.get("files", [])

    _log("=" * 60)
    _log(f"  Creating Auto-Fix PR for run #{run_id}")
    _log(f"  Repo: {repo}")
    _log(f"  Branch: {branch_name}")
    _log(f"  Strategy: {strategy}")
    _log(f"  Files: {', '.join(files[:5])}")
    _log("=" * 60)

    # ── Sanity checks ────────────────────────────────────────────────
    if not _check_git_installed():
        raise RuntimeError("Git CLI not installed — cannot create PR.")
    if not _check_gh_installed():
        raise RuntimeError("GitHub CLI not installed — cannot create PR.")

    project = _PROJECT_DIR
    if not os.path.isdir(os.path.join(project, ".git")):
        raise RuntimeError(f"Not a git repository: {project}")

    base = _GIT_BASE_BRANCH
    worktree_path = os.path.join(_WORKTREE_DIR, f"run-{run_id}")
    worktree_path_norm = os.path.normpath(worktree_path)

    # ================================================================
    #  Fix #1: Dung Git Worktree de cach ly tung tien trinh
    # ================================================================
    #  race condition cu:
    #    Thread A: git checkout base, git checkout -b fix/1, git add, git commit
    #    Thread B: git checkout base  <--- xung dot index.lock
    #
    #  Giai phap:
    #    Thread A: git worktree add ../worktree/run-1 -> thao tac o ../worktree/run-1
    #    Thread B: git worktree add ../worktree/run-2 -> thao tac o ../worktree/run-2
    #    => Hoan toan doc lap, khong xung dot
    # ================================================================

    # ── Step 0: Dam bao worktree base dir ton tai ───────────────────
    os.makedirs(_WORKTREE_DIR, exist_ok=True)

    # ── Step 1: Tao worktree ─────────────────────────────────────────
    _log(f"Creating git worktree at {worktree_path_norm}...")

    # Xoa worktree cu neu con sot lai (idempotent)
    if os.path.isdir(worktree_path_norm):
        _log(f"Removing stale worktree at {worktree_path_norm}...")
        try:
            _run_git(["worktree", "remove", "--force", worktree_path_norm])
        except Exception:
            pass
        try:
            shutil.rmtree(worktree_path_norm, ignore_errors=True)
        except Exception:
            pass

    # Fetch latest base branch
    _run_git(["fetch", _GIT_REMOTE, base])

    # Tao worktree tu branch base
    wt_result = _run_git(["worktree", "add", worktree_path_norm, f"{_GIT_REMOTE}/{base}"])
    if wt_result.returncode != 0:
        raise RuntimeError(
            f"git worktree add failed: {wt_result.stderr[:300]}"
        )

    try:
        # ── Step 2: Create & switch to auto-fix branch trong worktree ──
        # Tao branch moi tu base (bo qua xoa branch cu vi chua chac ton tai)
        _run_git(["checkout", "-b", branch_name], cwd=worktree_path_norm)

        # ── Step 3: git add + commit (trong worktree) ─────────────────
        if files:
            for f in files:
                abs_f = os.path.normpath(os.path.join(project, f))
                wt_f = os.path.normpath(os.path.join(worktree_path_norm, f))
                # Copy file da fix tu project vao worktree
                if os.path.isfile(abs_f):
                    os.makedirs(os.path.dirname(wt_f), exist_ok=True)
                    shutil.copy2(abs_f, wt_f)
                    _run_git(["add", f], cwd=worktree_path_norm)
            _log(f"Staged {len(files)} file(s) from fix plan")

        # Attempt commit directly (git status can be race-y in worktrees)

        commit_msg = (
            f"auto-fix: {strategy} — {summary[:80]}\n\n"
            f"Triggered by CI run #{run_id} ({repo})\n"
            f"Strategy: {strategy}\n"
            f"QA Review: {qa_feedback[:300]}\n"
            f"Generated by Super Agent Multi-Agent Orchestrator (Phase 7)"
        )
        result = _run_git(["commit", "-m", commit_msg], cwd=worktree_path_norm)
        commit_sha = ""
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                m = re.search(r"\[[^\]]+\s+([a-f0-9]{7,40})\]", line)
                if m:
                    commit_sha = m.group(1)
                    break
            if not commit_sha:
                commit_sha = _run_git(["rev-parse", "HEAD"], cwd=worktree_path_norm).stdout.strip()
            _log(f"Committed: {commit_sha}")
        else:
            err = result.stderr[:300] if result.stderr else "(commit rejected)"
            raise RuntimeError(f"Git commit failed: {err}")

        # ── Step 4: Push to remote ───────────────────────────────────
        _log(f"Pushing {branch_name} to {_AUTO_FIX_REMOTE}...")
        push_result = _run_git(["push", _AUTO_FIX_REMOTE, branch_name], cwd=worktree_path_norm)
        if push_result.returncode != 0:
            raise RuntimeError(f"Git push failed: {push_result.stderr[:300]}")

        # ── Step 5: Create PR via gh ─────────────────────────────────
        pr_title = f"Auto-fix for CI Run #{run_id} ({strategy})"
        pr_body = (
            f"## 🤖 Auto-Generated Fix\n\n"
            f"**Trigger:** CI Run #{run_id} **FAILED**\n"
            f"**Repo:** {repo}\n"
            f"**Strategy:** `{strategy}`\n\n"
            f"**Summary:**\n{summary}\n\n"
            f"**Files Modified:**\n"
            + "\n".join(f"- `{f}`" for f in files[:10])
            + f"\n\n**QA Review:**\n```\n{qa_feedback[:500]}\n```\n"
            + f"\n---\n*Generated by Super Agent Multi-Agent Orchestrator (Phase 7)*"
        )

        _log("Creating PR via gh pr create...")
        gh_result = _run_gh([
            "pr", "create",
            "--repo", repo,
            "--base", base,
            "--head", branch_name,
            "--title", pr_title,
            "--body", pr_body,
        ])

        if gh_result.returncode != 0:
            raise RuntimeError(f"gh pr create failed: {gh_result.stderr[:300]}")

        pr_url = gh_result.stdout.strip()
        _log(f"PR created: {pr_url}")

        # ── Fix #3: KHONG record VERIFIED_PATTERN o day ──────────────
        #  Ly do: Code moi chi duoc QA agent approve (local), chua CI pass
        #  Record chi xay ra trong process_webhook() khi conclusion=success
        #  Xem xu ly CI SUCCESS ben duoi.
        _log("PatternStore recording deferred until CI passes (Fix #3)")

        return {
            "pr_url": pr_url,
            "branch": branch_name,
            "commit_sha": commit_sha,
            "status": "created",
        }

    finally:
        # ── Step 6: Luon dọn dẹp worktree (tranh ton tai nguyen) ────
        _log(f"Cleaning up worktree at {worktree_path_norm}...")
        try:
            # Switch ve base truoc khi remove worktree (force de tranh conflict)
            _run_git(["checkout", "--force", base], cwd=project, capture=False)
        except Exception:
            pass
        try:
            _run_git(["worktree", "remove", "--force", worktree_path_norm], capture=False)
        except Exception:
            pass
        try:
            shutil.rmtree(worktree_path_norm, ignore_errors=True)
        except Exception:
            pass
        _log("Worktree cleaned up")


# ═══════════════════════════════════════════════════════════════════════════
#  7. Core Webhook Processing (Phase 7)
# ═══════════════════════════════════════════════════════════════════════════

def process_webhook(result: dict) -> dict:
    """
    Process webhook event va thuc thi Auto-Fix pipeline.

    Flow:
      1. workflow_run + conclusion=failure
         -> fetch logs -> orchestrator -> create_auto_fix_pr
      2. workflow_run + conclusion=success
         -> Record VERIFIED_PATTERN (CHI o day, Fix #3)
      3. Other -> ignored

    Fix #3 (Premature Verification):
      PatternStore.record_fix() CHI duoc goi khi CI pass (conclusion=success),
      KHONG goi sau khi tao PR.
    """
    event = result.get("event", "")
    conclusion = result.get("conclusion", "")
    branch = result.get("branch", "")
    repo = result.get("repo", _DEFAULT_REPO)
    run_id = result.get("run_id", 0)
    workflow = result.get("workflow", "")
    html_url = result.get("html_url", "")

    response: dict = {
        "processed": True,
        "event": event,
        "conclusion": conclusion,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "actions_taken": [],
        "summary": "",
    }

    # ────────────────────────────────────────────────────────────────
    #  CI SUCCESS -> Record VERIFIED_PATTERN (Fix #3)
    #                + cap nhat pending_fix status (Fix #5)
    # ────────────────────────────────────────────────────────────────
    if event == "workflow_run" and conclusion == "success":
        response["summary"] = f"CI SUCCESS for {branch}"
        response["actions_taken"].append("Workflow passed — no action needed")

        # Chi record pattern khi auto-fix branch CI pass
        # Fix #3: Day la noi DUY NHAT duoc ghi pattern
        if branch.lower().startswith(_AUTO_FIX_PREFIX):
            # ── Fix #5: Tra cuu pending_fix de lay context ─────────────
            pending = _lookup_pending_fix(branch)
            if pending:
                response["actions_taken"].append(
                    f"Found pending fix: run #{pending['run_id']} "
                    f"(strategy={pending['strategy']})"
                )
                # Cap nhat status -> ci_passed
                _update_pending_fix_status(branch, "ci_passed", pr_url=pending.get("pr_url", ""))

            # ── Ghi VERIFIED_PATTERN (chi khi PatternStore co san) ────
            if _pattern_store:
                try:
                    # Lay strategy + files tu pending fix neu co
                    strategy_label = pending.get("strategy", "") if pending else ""
                    file_paths = pending.get("files", "") if pending else workflow

                    fix_desc = branch.replace("auto-fix/run-", "run-") \
                                     .replace("-", " ").replace("_", " ")[:120]
                    _pattern_store.record_fix(
                        error_type="ci_verified",
                        error_description=f"Auto-fix passed CI: {fix_desc}",
                        error_context=(
                            f"Workflow: {workflow}\n"
                            f"Branch: {branch}\n"
                            f"Run: {html_url}\n"
                            f"Strategy: {strategy_label}\n"
                            f"Verified by: CI/CD (real pass)"
                        ),
                        fix_diff="CI passed for auto-fix branch",
                        fix_description="CI/CD verified fix pattern (CI pass)",
                        file_path=file_paths,
                        approved_by="ci-passed",
                    )
                    response["actions_taken"].append(
                        f"Recorded VERIFIED_PATTERN for {branch} (CI-passed)"
                    )
                    _log(f"VERIFIED_PATTERN recorded for {branch} — CI PASSED")
                except Exception as e:
                    _log(f"PatternStore record error: {e}", "WARN")
        else:
            _log(f"CI SUCCESS for {branch} — not auto-fix branch, no action")

        return response

    # ────────────────────────────────────────────────────────────────
    #  CI FAILURE -> Auto-Fix Pipeline
    # ────────────────────────────────────────────────────────────────
    if event == "workflow_run" and conclusion == "failure":
        _log(f"{'='*60}")
        _log(f"  CI FAILURE DETECTED")
        _log(f"  Repo: {repo}")
        _log(f"  Run #{run_id}")
        _log(f"  Branch: {branch}")
        _log(f"  Workflow: {workflow}")
        _log(f"  URL: {html_url}")
        _log(f"{'='*60}")

        response["actions_taken"].append(f"CI FAILURE: {workflow} run #{run_id}")

        # ── Step 1: Fetch error logs ────────────────────────────────
        bug_report = ""
        try:
            bug_report = fetch_github_action_logs(repo, run_id)
            response["actions_taken"].append(
                f"Fetched {len(bug_report)} chars (smart-extracted)"
            )
        except RuntimeError as e:
            _log(f"Failed to fetch logs: {e}", "ERROR")
            response["actions_taken"].append(f"Log fetch failed: {str(e)[:100]}")
            response["summary"] = f"CI FAILED for {branch} — log fetch error"
            return response
        except Exception as e:
            _log(f"Unexpected log fetch error: {e}", "ERROR")
            response["actions_taken"].append(f"Log fetch error: {str(e)[:100]}")
            response["summary"] = f"CI FAILED for {branch} — log fetch error"
            return response

        if not bug_report.strip():
            _log("Empty bug report after log fetch — cannot proceed", "WARN")
            response["actions_taken"].append("Empty log — skipping orchestrator")
            response["summary"] = f"CI FAILED for {branch} — empty logs"
            return response

        # ── Step 2: Call Orchestrator ────────────────────────────────
        orchestrator_result = None
        try:
            orchestrator_result = _call_orchestrator(bug_report)
            response["actions_taken"].append(
                f"Orchestrator: {orchestrator_result.get('iterations', '?')} iteration(s), "
                f"strategy={orchestrator_result.get('fix_plan', {}).get('strategy', '?')}"
            )
        except RuntimeError as e:
            _log(f"Orchestrator FAILED: {e}", "ERROR")
            response["actions_taken"].append(f"Orchestrator failed: {str(e)[:200]}")
            response["summary"] = f"CI FAILED for {branch} — orchestrator could not fix"
            return response
        except Exception as e:
            _log(f"Orchestrator crash: {e}", "ERROR")
            response["actions_taken"].append(f"Orchestrator crash: {str(e)[:100]}")
            response["summary"] = f"CI FAILED for {branch} — orchestrator crashed"
            return response

        # ── Step 3: Neu APPROVED -> Save pending fix + Create PR ────
        if orchestrator_result.get("success"):
            fix_plan = orchestrator_result.get("fix_plan", {})
            qa_feedback = orchestrator_result.get("qa_feedback", "")
            branch_name = f"auto-fix/run-{run_id}"

            # ── Fix #5: Luu pending fix vao DB truoc khi tao PR ──────
            _save_pending_fix(
                run_id=run_id,
                repo=repo,
                branch=branch_name,
                workflow=workflow,
                html_url=html_url,
                fix_plan=fix_plan,
                qa_feedback=qa_feedback,
            )

            try:
                pr_result = create_auto_fix_pr(
                    repo=repo,
                    run_id=run_id,
                    branch_name=branch_name,
                    fix_plan=fix_plan,
                    qa_feedback=qa_feedback,
                )
                response["pr_url"] = pr_result.get("pr_url", "")
                response["branch"] = branch_name
                response["commit_sha"] = pr_result.get("commit_sha", "")

                if pr_result.get("status") == "created":
                    # Cap nhat pending_fix status -> pr_created
                    _update_pending_fix_status(branch_name, "pr_created",
                                               pr_url=pr_result.get("pr_url", ""))
                    response["actions_taken"].append(
                        f"PR created: {pr_result['pr_url']}"
                    )
                    response["summary"] = (
                        f"CI FAILED -> Auto-fix PR created: {pr_result['pr_url']}"
                    )

                    # ✅ SUCCESS: Alert khi tao PR thanh cong
                    strategy = fix_plan.get("strategy", "?")
                    files_summary = ", ".join(fix_plan.get("files", [])[:5])
                    send_telegram_alert(
                        f"✅ *Auto-Fix PR Created*\n"
                        f"Repo: `{repo}`\n"
                        f"Branch: `{branch_name}`\n"
                        f"Run: [#{run_id}]({html_url})\n"
                        f"PR: [{pr_result['pr_url']}]({pr_result['pr_url']})\n"
                        f"Strategy: `{strategy}`\n"
                        f"Files: `{files_summary}`"
                    )
                else:
                    response["actions_taken"].append(
                        f"PR not created: {pr_result.get('reason', 'unknown')}"
                    )
                    response["summary"] = f"CI FAILED for {branch} — {pr_result.get('reason', 'no PR')}"

            except RuntimeError as e:
                _log(f"PR creation failed: {e}", "ERROR")
                response["actions_taken"].append(f"PR creation failed: {str(e)[:200]}")
                response["summary"] = f"CI FAILED for {branch} — fix ready but PR failed"
            except Exception as e:
                _log(f"PR creation crashed: {e}", "ERROR")
                response["actions_taken"].append(f"PR creation crash: {str(e)[:100]}")
                response["summary"] = f"CI FAILED for {branch} — PR creation crash"
        else:
            _log("Orchestrator did not produce a successful fix", "WARN")
            response["actions_taken"].append("Orchestrator failed to produce fix")
            response["summary"] = f"CI FAILED for {branch} — orchestrator could not fix"
            # ❌ FAILED: Alert khi orchestrator khong tao duoc fix
            send_telegram_alert(
                f"❌ *Auto-Fix Failed*\n"
                f"Repo: `{repo}`\n"
                f"Branch: `{branch}`\n"
                f"Run: [#{run_id}]({html_url})\n"
                f"Status: Orchestrator could not produce a fix\n"
                f"Workflow: `{workflow}`"
            )

        return response

    # ────────────────────────────────────────────────────────────────
    #  Unhandled event
    # ────────────────────────────────────────────────────────────────
    response["summary"] = f"Ignored event: {event}/{conclusion}"
    return response


# ═══════════════════════════════════════════════════════════════════════════
#  8. Daemon Mode — HTTP Server cho Cloudflare Tunnel
# ═══════════════════════════════════════════════════════════════════════════

def start_webhook_server(port: int = 11999, secret: str = ""):
    """Start a minimal HTTP server for webhook."""
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
    except ImportError:
        print("!! http.server not available", file=sys.stderr)
        return

    class _WebhookHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length)

            sig = self.headers.get("X-Hub-Signature-256", "")
            if not verify_signature(body, sig, secret):
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b'{"error":"signature mismatch"}')
                _log("Signature verification FAILED", "WARN")
                return

            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error":"invalid JSON"}')
                _log("Invalid JSON payload", "WARN")
                return

            result = parse_workflow_run(payload)
            if result:
                _log(f"Received: {result.get('conclusion', '?')} "
                     f"run #{result.get('run_id', '?')} "
                     f"branch={result.get('branch', '?')}")

                # 🚨 TRIGGERED: Alert khi nhan duoc webhook
                send_telegram_alert(
                    f"🚨 *Webhook Received*\n"
                    f"Repo: `{result.get('repo', '?')}`\n"
                    f"Branch: `{result.get('branch', '?')}`\n"
                    f"Run: [#{result.get('run_id', '?')}]({result.get('html_url', '')})\n"
                    f"Conclusion: `{result.get('conclusion', '?')}`\n"
                    f"Workflow: `{result.get('workflow', '?')}`"
                )

                try:
                    response = process_webhook(result)
                    status_code = 200
                except Exception as e:
                    _log(f"Webhook processing error: {e}", "ERROR")
                    # 💥 CRASH: Alert khi exception bat ngo
                    send_telegram_alert(
                        f"💥 *Webhook Processing Crashed*\n"
                        f"Repo: `{result.get('repo', '?')}`\n"
                        f"Run: [#{result.get('run_id', '?')}]({result.get('html_url', '')})\n"
                        f"Error: `{str(e)[:200]}`"
                    )
                    response = {"processed": False, "error": str(e)[:200]}
                    status_code = 500

                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8"))
                _log(f"Response: {response.get('summary', '?')}")
            else:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"processed":false,"reason":"ignored event"}')
                _log("Ignored event (not workflow_run or auto-fix branch)")

        def log_message(self, fmt, *args):
            pass

    # ── Fix #5: Don dep worktree con sot lai truoc khi lang nghe ──
    _cleanup_stale_worktrees()

    server = HTTPServer(("0.0.0.0", port), _WebhookHandler)
    _log(f"Webhook handler listening on port {port}")
    _log(f"Register in GitHub: https://<tunnel-url>/webhook")
    _log(f"Requires: gh CLI, git CLI, GITHUB_TOKEN env")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("Server stopped by user")
        server.server_close()


# ═══════════════════════════════════════════════════════════════════════════
#  9. Health Check
# ═══════════════════════════════════════════════════════════════════════════

def _get_github_token() -> str:
    """Lay GITHUB_TOKEN tu nhieu nguon env khac nhau."""
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        return token
    token = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if token:
        return token
    token = os.environ.get("GH_TOKEN", "")
    if token:
        return token
    return ""


def run_health_check() -> dict:
    """Self-test dependencies va environment."""
    checks = {
        "python": sys.version.split()[0],
        "git": _check_git_installed(),
        "gh": _check_gh_installed(),
        "GITHUB_TOKEN": bool(_get_github_token()),
        "orchestrator": HAS_ORCHESTRATOR,
        "pattern_store": _pattern_store is not None,
        "project_dir": os.path.isdir(_PROJECT_DIR),
        "project_has_git": os.path.isdir(os.path.join(_PROJECT_DIR, ".git")),
        "DEV_DIR": os.path.isdir(_DEV_DIR),
        "SUPER_AGENT_DIR": os.path.isdir(_SUPER_AGENT_DIR),
        # Fix #1 guards
        "worktree_dir_exists": os.path.isdir(_WORKTREE_DIR) or True,  # created on demand
        # Fix #2 guards
        "auto_fix_prefix": _AUTO_FIX_PREFIX,
        # Fix #3 guards
        "verify_only_on_ci_pass": True,
        # Fix #4 guards
        "error_patterns_loaded": _ERROR_TRIGGER_PATTERNS.pattern is not None,
        "error_context_lines": _ERROR_CONTEXT_LINES,
    }
    all_system = {k: v for k, v in checks.items()
                  if k not in ("python", "auto_fix_prefix",
                               "verify_only_on_ci_pass",
                               "error_patterns_loaded", "error_context_lines",
                               "worktree_dir_exists")}
    checks["all_ok"] = all(v is True for v in all_system.values())
    return checks


# ═══════════════════════════════════════════════════════════════════════════
#  10. CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="CI/CD Auto-Fix Webhook Bridge (Phase 7, Production-Ready)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Production Guards:
              - Fix #1: git worktree (tranh race condition Git)
              - Fix #2: auto-fix/ branch filter (ngan infinite loop)
              - Fix #3: VERIFIED_PATTERN chi record khi CI PASS that su
              - Fix #4: Regex smart error extraction (thay blind tail truncation)

            Examples:
              python webhook_handler.py --daemon --port 11999 --secret "s3cr3t"
              python webhook_handler.py --health
              python webhook_handler.py --test-event workflow_run --test-conclusion failure
              python webhook_handler.py --fetch-logs thetime1102/nhatvi-ecosystem-dev 12345
        """),
    )
    parser.add_argument("--daemon", action="store_true", help="Start webhook server")
    parser.add_argument("--port", type=int, default=11999, help="Listen port")
    parser.add_argument("--secret", default="", help="Webhook secret (HMAC-SHA256)")
    parser.add_argument("--test-payload", help="Test with a JSON payload file")
    parser.add_argument("--test-event", default="workflow_run",
                        choices=["workflow_run", "push", "pull_request"])
    parser.add_argument("--test-conclusion", default="success",
                        choices=["success", "failure", "cancelled"])
    parser.add_argument("--health", action="store_true", help="Run health check")
    parser.add_argument("--fetch-logs", nargs=2, metavar=("REPO", "RUN_ID"),
                        help="Fetch logs for a specific run")
    parser.add_argument("--max-chars", type=int, default=_MAX_LOG_CHARS,
                        help=f"Max log chars (default: {_MAX_LOG_CHARS})")

    args = parser.parse_args()

    # ── Health check ────────────────────────────────────────────────
    if args.health:
        print(json.dumps(run_health_check(), indent=2, ensure_ascii=False))
        return

    # ── Fetch logs standalone ───────────────────────────────────────
    if args.fetch_logs:
        repo, run_id_str = args.fetch_logs
        try:
            run_id = int(run_id_str)
        except ValueError:
            print(f"Invalid run_id: {run_id_str}", file=sys.stderr)
            sys.exit(1)
        try:
            logs = fetch_github_action_logs(repo, run_id, max_chars=args.max_chars)
            print(logs)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # ── Daemon mode ─────────────────────────────────────────────────
    if args.daemon:
        start_webhook_server(args.port, args.secret)
        return

    # ── Test with payload file ──────────────────────────────────────
    if args.test_payload:
        with open(args.test_payload, "r", encoding="utf-8") as f:
            payload = json.load(f)
        result = parse_workflow_run(payload)
        if result:
            _log("Parsed result:")
            print(json.dumps(result, indent=2, ensure_ascii=False))
            response = process_webhook(result)
            _log("\nActions taken:")
            print(json.dumps(response, indent=2, ensure_ascii=False))
        else:
            _log("Event ignored")
        return

    # ── Manual test with mock ───────────────────────────────────────
    mock = {
        "event": args.test_event,
        "action": "completed",
        "conclusion": args.test_conclusion,
        "branch": "dev",
        "sha": "abc123def456",
        "repo": _DEFAULT_REPO,
        "workflow": "CI Test Suite",
        "run_id": 12345,
        "html_url": f"https://github.com/{_DEFAULT_REPO}/actions/runs/12345",
        "logs_url": f"https://api.github.com/repos/{_DEFAULT_REPO}/actions/jobs/test/logs",
    }
    _log(f"Testing with mock event: {args.test_event}:{args.test_conclusion}")
    print(f"Mock payload:\n{json.dumps(mock, indent=2, ensure_ascii=False)}")
    print()

    try:
        response = process_webhook(mock)
        print(json.dumps(response, indent=2, ensure_ascii=False))
    except Exception as e:
        _log(f"Test failed: {e}", "ERROR")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
