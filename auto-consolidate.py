#!/usr/bin/env python3
"""
auto-consolidate.py — Super Agent Auto Memory Consolidation Engine
=================================================================
Event-Driven: chạy khi có commit / push / cron 5 phút.

Pipeline:
  1. git diff (từ lần check cuối) → detect changed files
  2. super-agent git-index → update embeddings
  3. LLM phân loại changes → 3 categories:
     - bugs: lỗi đã fix (mô tả + file + nguyên nhân)
     - config: cấu hình thay đổi (key + giá trị cũ/mới)
     - features: tính năng mới (mô tả + files affected)
  4. Lưu vào memory_consolidation table (JSON)
  5. Update MEMORY.md tóm tắt
  6. Alert nếu phát hiện breaking changes
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# ─── Paths ────────────────────────────────────────────────────────────────
WORKSPACE = r"C:\Users\tqv11\.openclaw\workspace"
DEV_DIR = os.path.join(WORKSPACE, "nhatvi-ecosystem-dev")
MEMORY_DB = os.path.join(WORKSPACE, "memory", "memory.db")
CONSOLIDATION_DB = os.path.join(WORKSPACE, "memory", "consolidation.db")
STATE_FILE = os.path.join(WORKSPACE, "memory", ".consolidation_state.json")

# ─── DB Schema ─────────────────────────────────────────────────────────────

def init_consolidation_db():
    """Create consolidation tracking tables."""
    os.makedirs(os.path.dirname(CONSOLIDATION_DB), exist_ok=True)
    conn = sqlite3.connect(CONSOLIDATION_DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS consolidation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('bug','config','feature','lesson','decision')),
            title TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            files_affected TEXT DEFAULT '[]',
            severity TEXT DEFAULT 'info' CHECK(severity IN ('critical','warning','info')),
            source TEXT DEFAULT 'auto' CHECK(source IN ('auto','manual','webhook')),
            commit_sha TEXT DEFAULT '',
            created_at INTEGER DEFAULT (unixepoch())
        );
        CREATE INDEX IF NOT EXISTS idx_consolidation_date ON consolidation(date);
        CREATE INDEX IF NOT EXISTS idx_consolidation_category ON consolidation(category);
        
        CREATE TABLE IF NOT EXISTS consolidation_tracking (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def get_last_processed_sha() -> str:
    """Get the last commit SHA we processed."""
    conn = sqlite3.connect(CONSOLIDATION_DB)
    cur = conn.execute("SELECT value FROM consolidation_tracking WHERE key='last_sha'")
    row = cur.fetchone()
    conn.close()
    return row[0] if row else ""


def set_last_processed_sha(sha: str):
    conn = sqlite3.connect(CONSOLIDATION_DB)
    conn.execute("INSERT OR REPLACE INTO consolidation_tracking (key, value) VALUES ('last_sha', ?)", (sha,))
    conn.commit()
    conn.close()


# ─── Git Diff ──────────────────────────────────────────────────────────────

def get_git_diff(git_dir: str, since_sha: str = "") -> dict:
    """Get files changed since last processed SHA."""
    if not os.path.isdir(os.path.join(git_dir, ".git")):
        return {"files": [], "commits": [], "from_sha": "", "to_sha": ""}

    # Get current HEAD SHA
    r = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, timeout=15, cwd=git_dir)
    current_sha = r.stdout.decode("utf-8", errors="replace").strip()
    if not current_sha:
        return {"files": [], "commits": [], "from_sha": "", "to_sha": ""}

    # If no previous SHA, get last 5 commits
    try:
        if since_sha:
            base_ref_test = subprocess.run(
                ["git", "rev-parse", "--verify", since_sha],
                capture_output=True, timeout=10, cwd=git_dir,
            )
            if base_ref_test.returncode != 0:
                since_sha = ""
    except Exception:
        since_sha = ""

    base_ref = since_sha if since_sha else f"{current_sha}~5"

    try:
        r = subprocess.run(
            ["git", "diff", "--name-status", f"{base_ref}..{current_sha}"],
            capture_output=True, timeout=15, cwd=git_dir,
        )
        output = r.stdout.decode("utf-8", errors="replace")
        files = []
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 2:
                status = parts[0]
                path = parts[1]
                files.append({"path": path, "status": status})

        # Get commit messages
        r2 = subprocess.run(
            ["git", "log", f"{base_ref}..{current_sha}", "--oneline", "--no-decorate"],
            capture_output=True, timeout=15, cwd=git_dir,
        )
        log_output = r2.stdout.decode("utf-8", errors="replace")
        commits = [c.strip() for c in log_output.strip().split("\n") if c.strip()]

        return {
            "files": files,
            "commits": commits,
            "from_sha": base_ref,
            "to_sha": current_sha,
        }
    except Exception as e:
        print(f"   Git diff error: {e}")
        return {"files": [], "commits": [], "from_sha": "", "to_sha": current_sha}


# ─── LLM Consolidation ────────────────────────────────────────────────────

def classify_changes(diff_data: dict, context: str = "code:dev") -> list:
    """
    Phân loại code changes thành bugs/config/features.
    Dùng quy tắc heuristic (không cần LLM API để tránh tốn kém).
    """
    entries = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    commit_msgs = diff_data.get("commits", [])
    files = diff_data.get("files", [])

    for commit in commit_msgs:
        msg = commit.split(" ", 1)[-1] if " " in commit else commit
        sha = commit.split(" ")[0] if " " in commit else ""

        # Heuristic classification
        cat = "feature"
        severity = "info"

        lmsg = msg.lower()
        if any(w in lmsg for w in ["fix", "bug", "hotfix", "crash", "error", "rollback", "revert"]):
            cat = "bug"
            severity = "critical" if any(w in lmsg for w in ["crash", "hotfix", "rollback"]) else "warning"
        elif any(w in lmsg for w in ["config", "env", "setting", "migration", "database"]):
            cat = "config"
            severity = "warning" if "migration" in lmsg else "info"
        elif any(w in lmsg for w in ["feat", "feature", "add", "new", "create"]):
            cat = "feature"
        elif any(w in lmsg for w in ["docs", "readme", "comment"]):
            cat = "feature"  # docs improvements
            severity = "info"

        # Files affected
        affected = [f["path"] for f in files if f.get("status") in ("A", "M")]

        entries.append({
            "date": now,
            "category": cat,
            "title": msg[:120],
            "description": f"Commit {sha[:8]}: {msg}",
            "files_affected": json.dumps(affected[:10]),
            "severity": severity,
            "source": "auto",
            "commit_sha": sha[:12] if sha else "",
        })

    return entries


# ─── Consolidation Runner ─────────────────────────────────────────────────

def run_consolidation(context: str = "code:dev", source: str = "auto"):
    """Main consolidation pipeline."""
    print(f"** Consolidation [{source}] starting...")

    # Step 0: Init DB
    init_consolidation_db()

    # Step 1: Get last processed SHA
    last_sha = get_last_processed_sha()
    print(f"   Last SHA: {last_sha or '(none)'}")

    # Step 2: Git diff
    diff = get_git_diff(DEV_DIR, last_sha)
    if not diff or not diff.get("commits"):
        print("   No new commits to process")
        return False

    print(f"   New commits: {len(diff['commits'])}")
    print(f"   Changed files: {len(diff['files'])}")

    # Step 3: Index code changes via super-agent
    print("   Indexing changed files...")
    try:
        result = subprocess.run(
            [sys.executable, os.path.join(os.path.dirname(__file__), "super_agent.py"),
             "git-index", diff["to_sha"], "--context", context],
            capture_output=True, timeout=120,
            cwd=os.path.dirname(__file__),
        )
        out = result.stdout.decode("utf-8", errors="replace")
        for line in out.strip().split("\n")[-5:]:
            if line.strip():
                print(f"      {line}")
    except Exception as e:
        print(f"   Index error: {e}")

    # Step 4: Classify changes
    entries = classify_changes(diff, context)
    print(f"   Classified: {len(entries)} entries")

    # Step 5: Save to consolidation DB
    conn = sqlite3.connect(CONSOLIDATION_DB)
    for entry in entries:
        conn.execute(
            """INSERT INTO consolidation (date, category, title, description, files_affected, severity, source, commit_sha)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry["date"], entry["category"], entry["title"], entry["description"],
             entry["files_affected"], entry["severity"], entry["source"], entry["commit_sha"]),
        )
    conn.commit()
    conn.close()

    # Step 6: Update tracking
    set_last_processed_sha(diff["to_sha"])

    # Step 7: Summary
    cats = {}
    for e in entries:
        cats[e["category"]] = cats.get(e["category"], 0) + 1

    print(f"** Consolidation complete!")
    print(f"   Categories: {json.dumps(cats)}")
    print(f"   Entries saved: {len(entries)}")

    # Alert for critical
    critical = [e for e in entries if e["severity"] == "critical"]
    if critical:
        print(f"\n   !! CRITICAL: {len(critical)} critical entries!")
        for e in critical:
            print(f"      - {e['title']}")

    return True


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Super Agent Auto Memory Consolidation")
    parser.add_argument("--source", default="auto", choices=["auto", "webhook", "manual"])
    parser.add_argument("--context", default="code:dev")
    args = parser.parse_args()

    run_consolidation(args.context, args.source)


if __name__ == "__main__":
    main()
