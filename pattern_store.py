#!/usr/bin/env python3
from __future__ import annotations
"""
pattern_store.py - VERIFIED_PATTERN Learning Engine
====================================================
Core module for "học từ lệnh Approve/Apply".

Kiến trúc:
  [Approve/Apply Event]
        ↓
  1. record_fix(error_context, fix_diff, metadata)
     → Lưu vào consolidated_patterns table (dedup bằng SHA256)
     → Gắn tag [VERIFIED_PATTERN]
        ↓
  2. search_similar(error_query, top_k=5)
     → Dùng simple token overlap scoring (không cần embedding)
     → Trả về kết quả có score > threshold
        ↓
  3. build_few_shot_prompt(similar_patterns)
     → Build "Đây là cách tao đã sửa lỗi tương tự trong quá khứ"
     → Inject vào system prompt của DeepSeek

Sử dụng:
  from pattern_store import PatternStore
  store = PatternStore()
  store.record_fix(error_type="race_condition", error_context=..., fix_diff=..., ...)
  patterns = store.search_similar("auto post worker crash")
  prompt = store.build_few_shot_examples(patterns)
"""



import hashlib
import json
import os
import re
import sqlite3
import time
from typing import Optional

# ─── Paths ────────────────────────────────────────────────────────────────
WORKSPACE = r"C:\Users\tqv11\.openclaw\workspace"
MEMORY_DIR = os.path.join(WORKSPACE, "memory")
CONSOLIDATION_DB = os.path.join(MEMORY_DIR, "consolidation.db")

# ─── DB Schema ─────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS verified_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER DEFAULT (unixepoch()),
    error_type TEXT NOT NULL DEFAULT 'unknown',
    error_description TEXT NOT NULL DEFAULT '',
    error_context TEXT NOT NULL DEFAULT '',
    repo_path TEXT NOT NULL DEFAULT '',
    branch TEXT NOT NULL DEFAULT '',
    commit_sha TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    line_start INTEGER DEFAULT 0,
    line_end INTEGER DEFAULT 0,
    original_code TEXT NOT NULL DEFAULT '',
    fix_diff TEXT NOT NULL DEFAULT '',
    fix_description TEXT NOT NULL DEFAULT '',
    pattern_hash TEXT NOT NULL UNIQUE,
    application_count INTEGER DEFAULT 1,
    last_applied INTEGER,
    approved_by TEXT DEFAULT 'human',
    verified INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_vp_type ON verified_patterns(error_type);
CREATE INDEX IF NOT EXISTS idx_vp_hash ON verified_patterns(pattern_hash);
CREATE INDEX IF NOT EXISTS idx_vp_applied ON verified_patterns(application_count DESC);

CREATE TABLE IF NOT EXISTS verified_patterns_tracking (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class PatternStore:
    """Verified Pattern Learning Engine."""

    def __init__(self, db_path: str = CONSOLIDATION_DB):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA_SQL)
        conn.commit()
        conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    # ─── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _compute_hash(error_context: str, fix_diff: str) -> str:
        """SHA256 hash of error_context + fix_diff for dedup."""
        material = (error_context.strip() + "\n---\n" + fix_diff.strip()).encode("utf-8")
        return hashlib.sha256(material).hexdigest()[:32]

    @staticmethod
    def _extract_token_set(text: str) -> set:
        """Extract keyword tokens for overlap scoring."""
        text_lower = text.lower()
        # Split words and keep meaningful tokens
        tokens = set(re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', text_lower))
        # Remove stopwords
        stopwords = {
            'the', 'this', 'that', 'and', 'for', 'with', 'from', 'have',
            'been', 'was', 'are', 'not', 'but', 'all', 'can', 'has',
            'its', 'set', 'get', 'put', 'add', 'new', 'use', 'using',
            'used', 'may', 'also', 'than', 'then', 'each', 'will',
        }
        return tokens - stopwords

    @staticmethod
    def _token_overlap_score(text_a: str, text_b: str) -> float:
        """Jaccard similarity on token sets."""
        set_a = PatternStore._extract_token_set(text_a)
        set_b = PatternStore._extract_token_set(text_b)
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    # ─── Core: Record a verified fix ─────────────────────────────────────

    def record_fix(
        self,
        error_type: str = "unknown",
        error_description: str = "",
        error_context: str = "",
        fix_diff: str = "",
        fix_description: str = "",
        file_path: str = "",
        line_start: int = 0,
        line_end: int = 0,
        original_code: str = "",
        repo_path: str = "",
        branch: str = "",
        commit_sha: str = "",
        approved_by: str = "human",
    ) -> dict:
        """
        Record a verified fix pattern.

        Args:
            error_type: Loại lỗi (race_condition, memory_leak, async_safety, etc.)
            error_description: Mô tả ngắn gọn lỗi
            error_context: Full context (scan report, error stack, etc.)
            fix_diff: Git diff hoặc unified diff của fix
            fix_description: Mô tả cách fix
            file_path: File bị lỗi
            line_start, line_end: Line range
            original_code: Code gốc trước khi fix
            repo_path, branch, commit_sha: Git metadata
            approved_by: 'human' | 'ci-passed'

        Returns:
            dict with id, is_new (True nếu mới, False nếu đã tồn tại)
        """
        pattern_hash = self._compute_hash(error_context or error_description, fix_diff)
        now = int(time.time())

        conn = self._get_conn()
        try:
            # Check if pattern already exists
            existing = conn.execute(
                "SELECT id, application_count FROM verified_patterns WHERE pattern_hash = ?",
                (pattern_hash,),
            ).fetchone()

            if existing:
                # Update: increment application count
                conn.execute(
                    "UPDATE verified_patterns SET application_count = application_count + 1, "
                    "last_applied = ?, fix_diff = ?, approved_by = ? WHERE id = ?",
                    (now, fix_diff, approved_by, existing[0]),
                )
                conn.commit()
                return {"id": existing[0], "is_new": False, "application_count": existing[1] + 1}

            # Insert new pattern
            cur = conn.execute(
                """INSERT INTO verified_patterns 
                   (error_type, error_description, error_context, pattern_hash,
                    fix_diff, fix_description, file_path, line_start, line_end,
                    original_code, repo_path, branch, commit_sha, approved_by,
                    last_applied)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    error_type, error_description[:500], error_context[:5000],
                    pattern_hash, fix_diff[:10000], fix_description[:500],
                    file_path, line_start, line_end,
                    original_code[:10000], repo_path, branch, commit_sha,
                    approved_by, now,
                ),
            )
            conn.commit()
            return {"id": cur.lastrowid, "is_new": True, "application_count": 1}

        finally:
            conn.close()

    # ─── Search: Find similar verified patterns ──────────────────────────

    def search_similar(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 0.15,
        error_type_filter: Optional[str] = None,
    ) -> list[dict]:
        """
        Search for similar VERIFIED_PATTERNs using token overlap scoring.

        Args:
            query: Error description hoặc context cần tìm
            top_k: Max results
            min_score: Minimum Jaccard similarity threshold
            error_type_filter: Filter by error type (optional)

        Returns:
            List of {id, error_type, error_description, fix_diff, score, application_count, ...}
        """
        conn = self._get_conn()
        try:
            cur = conn.execute("SELECT * FROM verified_patterns WHERE verified = 1 ORDER BY application_count DESC")
            col_names = [d[0] for d in cur.description]
            rows = cur.fetchall()

            # If type filter, filter in Python
            if error_type_filter:
                type_idx = col_names.index('error_type')
                rows = [r for r in rows if r[type_idx] == error_type_filter]

            # Score each pattern
            scored = []
            for row in rows:
                pattern = dict(zip(col_names, row))
                # Calculate score against query
                score = self._token_overlap_score(query, pattern.get("error_description", ""))
                # Also score against error_context for better matching
                ctx_score = self._token_overlap_score(query, pattern.get("error_context", ""))
                final_score = max(score, ctx_score * 0.8)

                if final_score >= min_score:
                    pattern["score"] = round(final_score, 4)
                    scored.append(pattern)

            # Sort by (score descending, application_count descending)
            scored.sort(key=lambda p: (p["score"], p["application_count"]), reverse=True)
            return scored[:top_k]

        finally:
            conn.close()

    # ─── Build few-shot prompt ───────────────────────────────────────────

    def build_few_shot_examples(self, patterns: list[dict]) -> str:
        """
        Build few-shot examples string from patterns.
        Được inject vào system prompt trước khi gọi DeepSeek.
        """
        if not patterns:
            return ""

        parts = [
            "=== VERIFIED FIX PATTERNS FROM PAST (Few-shot Examples) ===",
            "Các pattern dưới đây đã được kiểm chứng bởi con người hoặc CI/CD.",
            "Hãy DÙNG CHÚNG LÀM MẪU để fix lỗi tương tự:\n",
        ]

        for i, p in enumerate(patterns[:3], 1):
            parts.append(f"--- Example {i} ---")
            parts.append(f"Loại lỗi: {p.get('error_type', '?')}")
            parts.append(f"Mô tả: {p.get('error_description', '?')}")
            parts.append(f"File: {p.get('file_path', '?')} (lines {p.get('line_start', 0)}-{p.get('line_end', 0)})")
            parts.append(f"Đã dùng {p.get('application_count', 1)} lần thành công")
            parts.append(f"Cách fix:\n```diff\n{p.get('fix_diff', '')[:2000]}\n```")
            parts.append("")  # blank line

        parts.append("=== END OF VERIFIED FIX PATTERNS ===\n")
        return "\n".join(parts)

    # ─── Prune: Keep top N per error type ────────────────────────────────

    def prune_patterns(self, keep_top: int = 5) -> dict:
        """
        Delete patterns beyond top N per error type (by application_count).
        Returns {error_type: deleted_count}.
        """
        conn = self._get_conn()
        try:
            types = conn.execute(
                "SELECT DISTINCT error_type FROM verified_patterns"
            ).fetchall()

            result = {}
            for (et,) in types:
                if not et:
                    continue
                rows = conn.execute(
                    """SELECT id FROM verified_patterns
                       WHERE error_type = ?
                       ORDER BY application_count DESC, id DESC""",
                    (et,),
                ).fetchall()

                if len(rows) > keep_top:
                    delete_ids = [r[0] for r in rows[keep_top:]]
                    placeholders = ",".join("?" for _ in delete_ids)
                    conn.execute(
                        f"DELETE FROM verified_patterns WHERE id IN ({placeholders})",
                        delete_ids,
                    )
                    result[et] = len(delete_ids)
                else:
                    result[et] = 0

            conn.commit()
            return result
        finally:
            conn.close()

    # ─── Stats ────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get pattern statistics."""
        conn = self._get_conn()
        try:
            total = conn.execute("SELECT COUNT(*) FROM verified_patterns").fetchone()[0]
            by_type = conn.execute(
                "SELECT error_type, COUNT(*) as cnt, SUM(application_count) as total_apps "
                "FROM verified_patterns GROUP BY error_type ORDER BY cnt DESC"
            ).fetchall()
            most_used = conn.execute(
                "SELECT error_description, application_count FROM verified_patterns "
                "ORDER BY application_count DESC LIMIT 5"
            ).fetchall()
            return {
                "total_patterns": total,
                "by_type": [{"type": r[0], "count": r[1], "total_applications": r[2]} for r in by_type],
                "most_used": [{"description": r[0][:80], "times": r[1]} for r in most_used],
            }
        finally:
            conn.close()


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="PatternStore - VERIFIED_PATTERN Learning Engine")
    sub = parser.add_subparsers(dest="command")

    # record
    r = sub.add_parser("record", help="Record a verified fix pattern")
    r.add_argument("--type", required=True, help="Error type (race_condition|memory_leak|...)")
    r.add_argument("--description", required=True, help="Error description")
    r.add_argument("--context", default="", help="Error context")
    r.add_argument("--fix-diff", required=True, help="The fix diff")
    r.add_argument("--fix-description", default="", help="How was it fixed?")
    r.add_argument("--file", default="", help="File path")
    r.add_argument("--line-start", type=int, default=0)
    r.add_argument("--line-end", type=int, default=0)
    r.add_argument("--original-code", default="", help="Original code before fix")
    r.add_argument("--branch", default="", help="Git branch")
    r.add_argument("--commit", default="", help="Commit SHA")
    r.add_argument("--approved-by", default="human", choices=["human", "ci-passed"])

    # search
    s = sub.add_parser("search", help="Search similar patterns")
    s.add_argument("query", help="Search query")
    s.add_argument("--top-k", type=int, default=5)
    s.add_argument("--min-score", type=float, default=0.15)
    s.add_argument("--type", default="", help="Filter by error type")

    # stats
    sub.add_parser("stats", help="Show pattern statistics")

    # prune
    p_prune = sub.add_parser("prune", help="Keep top N patterns per error type")
    p_prune.add_argument("--keep", type=int, default=5, help="Max patterns per type")

    args = parser.parse_args()
    store = PatternStore()

    if args.command == "record":
        result = store.record_fix(
            error_type=args.type,
            error_description=args.description,
            error_context=args.context,
            fix_diff=args.fix_diff,
            fix_description=args.fix_description,
            file_path=args.file,
            line_start=args.line_start,
            line_end=args.line_end,
            original_code=args.original_code,
            branch=args.branch,
            commit_sha=args.commit,
            approved_by=args.approved_by,
        )
        status = "NEW" if result["is_new"] else "UPDATED"
        print(f"[PatternStore] {status}: pattern #{result['id']} (used {result['application_count']}x)")

    elif args.command == "search":
        results = store.search_similar(
            query=args.query,
            top_k=args.top_k,
            min_score=args.min_score,
            error_type_filter=args.type or None,
        )
        if not results:
            print("[PatternStore] No similar patterns found")
        else:
            print(f"[PatternStore] Found {len(results)} similar pattern(s):")
            for p in results:
                print(f"  #{p['id']} [{p['error_type']}] score={p['score']:.2f} used={p['application_count']}x")
                print(f"    {p['error_description'][:100]}")
                print(f"    File: {p['file_path']}:{p['line_start']}-{p['line_end']}")
                diff_preview = p['fix_diff'][:120].replace('\n', '\\n')
                print(f"    Fix: {diff_preview}...")
                print()

    elif args.command == "stats":
        stats = store.get_stats()
        print(f"[PatternStore] Stats:")
        print(f"  Total patterns: {stats['total_patterns']}")
        print(f"  By type:")
        for t in stats['by_type']:
            print(f"    {t['type']}: {t['count']} patterns, {t['total_applications']} applications")
        print(f"  Most used:")
        for m in stats['most_used']:
            print(f"    \"{m['description']}\" - used {m['times']}x")

    elif args.command == "prune":
        pruned = store.prune_patterns(args.keep)
        total = sum(pruned.values())
        if total == 0:
            print(f"[PatternStore] No patterns needed pruning (all within top {args.keep})")
        else:
            print(f"[PatternStore] Pruned {total} pattern(s):")
            for et, count in pruned.items():
                if count > 0:
                    print(f"  {et}: removed {count}")
        stats = store.get_stats()
        print(f"  Remaining: {stats['total_patterns']}")


if __name__ == "__main__":
    main()
