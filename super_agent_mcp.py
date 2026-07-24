#!/usr/bin/env python3
"""
super_agent_mcp.py — Super Agent MCP Server
=============================================
Đóng gói Semantic Memory Search (super_agent.py) + VERIFIED_PATTERN Learning
(PatternStore) thành MCP Server chạy qua stdio transport.

Giúp các AI Client (Claude Desktop, Cursor, etc.) gọi trực tiếp các tool:
  - search_memory            — Hybrid (vector + keyword) search trong code memory
  - search_verified_patterns — Tra cứu các VERIFIED_PATTERN đã học

Usage:
  python super_agent_mcp.py

Config (claude_desktop_config.json / Cursor mcp.json):
  {
    "mcpServers": {
      "super-agent-memory": {
        "command": "python",
        "args": ["C:/Users/tqv11/.openclaw/workspace/super-agent-plugin/super_agent_mcp.py"]
      }
    }
  }
"""

import io
import json
import math
import os
import re
import sqlite3
import struct
import sys
import textwrap
import traceback
import warnings
from typing import Any, Optional

warnings.filterwarnings("ignore")

# ─── KHÔNG wrap sys.stdout / sys.stderr ở global level ─────────────────
# MCP SDK cần quyền truy cập sys.stdout.buffer cho stdio_server().
# Việc search sẽ query DB trực tiếp thay vì capture stdout.

# ─── Path setup ─────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

# ─── Import core modules ─────────────────────────────────────────────────
try:
    import super_agent as sa
except ImportError as e:
    print(f"!! Failed to import super_agent: {e}", file=sys.stderr)
    sa = None  # type: ignore[assignment]

try:
    from pattern_store import PatternStore
except ImportError as e:
    print(f"!! Failed to import PatternStore: {e}", file=sys.stderr)
    PatternStore = None  # type: ignore[assignment,misc]

# ─── Import FastMCP ──────────────────────────────────────────────────────
try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print("!! MCP SDK not installed. Run: pip install mcp", file=sys.stderr)
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
#  FastMCP Server
# ═══════════════════════════════════════════════════════════════════════════
mcp = FastMCP("SuperAgentMemory")


# ─── Direct DB Search (không dùng stdout capture) ──────────────────────
def _cosine_sim(a_bytes: bytes, b_bytes: bytes) -> float:
    """Cosine similarity giữa 2 embedding vectors."""
    dim = len(a_bytes) // 4
    a = struct.unpack(f"{dim}f", a_bytes)
    b = struct.unpack(f"{dim}f", b_bytes)
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na * nb == 0:
        return 0.0
    return dot / (na * nb)


def _stored_path(filepath: str) -> str:
    """Rút gọn path về relative workspace."""
    if not filepath or not os.path.isabs(filepath):
        return filepath or "?"
    try:
        workspace = getattr(sa, "WORKSPACE", None) or ""
        if workspace:
            return os.path.relpath(filepath, workspace)
    except Exception:
        pass
    # Fallback: lấy tên file + 2 folder cuối
    parts = filepath.replace("\\", "/").split("/")
    if len(parts) > 3:
        return "/".join(parts[-3:])
    return filepath


def _search_direct(query: str, limit: int = 10) -> str:
    """
    Query DB trực tiếp — không capture stdout.
    Hybrid search: vector cosine similarity + keyword LIKE.
    """
    if sa is None:
        return "❌ super_agent module không khả dụng (import lỗi)."

    # Lấy DB connection
    try:
        conn = sa.get_db()
    except Exception as e:
        return f"❌ Không thể kết nối memory DB: {e}"

    results: list[tuple[float, str, str, str, bool]] = []
    seen_hashes: set[str] = set()

    try:
        # ── Bước 1: Vector Search ─────────────────────────────────
        try:
            conn.execute("SELECT memory_add_text(?, 'sys_srch')", (query,))
            conn.commit()
            cur_hash = conn.execute(
                "SELECT hash FROM dbmem_content WHERE context='sys_srch' LIMIT 1"
            ).fetchone()
            if cur_hash:
                cur_emb = conn.execute(
                    "SELECT embedding FROM dbmem_vault WHERE hash=?", (cur_hash[0],)
                ).fetchone()
                if cur_emb:
                    all_emb = conn.execute(
                        """SELECT v.hash, v.embedding, c.context
                           FROM dbmem_vault v
                           JOIN dbmem_content c ON v.hash = c.hash
                           WHERE c.context NOT LIKE 'sys_%'"""
                    ).fetchall()
                    scored = []
                    for v_hash, v_emb, ctx in all_emb:
                        sim = _cosine_sim(cur_emb[0], v_emb)
                        scored.append((sim, v_hash, ctx))
                    scored.sort(key=lambda x: -x[0])
                    for sim, v_hash, ctx in scored[: limit * 2]:
                        fp = v_hash
                        chunk_info = conn.execute(
                            "SELECT file_path FROM sa_chunks WHERE mem_hash=? LIMIT 1",
                            (v_hash,),
                        ).fetchone()
                        if chunk_info:
                            fp = chunk_info[0]
                        if v_hash not in seen_hashes:
                            seen_hashes.add(v_hash)
                            results.append((sim, fp, ctx, v_hash, True))
                # Cleanup search query
                conn.execute("SELECT memory_delete(?)", (cur_hash[0],))
                conn.commit()
        except Exception as e:
            # Vector search lỗi — silent fallthrough
            pass

        # ── Bước 2: Keyword Search ────────────────────────────────
        try:
            cur = conn.execute(
                """SELECT hash, path AS hpath, context, value
                   FROM dbmem_content
                   WHERE value LIKE ? AND context NOT LIKE 'sys_%'
                   LIMIT ?""",
                (f"%{query}%", limit),
            )
            for h, hpath, ctx, val in cur.fetchall():
                fp = hpath
                chunk_info = conn.execute(
                    "SELECT file_path FROM sa_chunks WHERE mem_hash=? LIMIT 1",
                    (h,),
                ).fetchone()
                if chunk_info:
                    fp = chunk_info[0]
                if h not in seen_hashes:
                    seen_hashes.add(h)
                    results.append((1.0, fp, ctx, h, False))
        except Exception:
            pass

        # ── Merge & Sort ──────────────────────────────────────────
        merged: list[tuple[float, str, str, str, bool]] = []
        seen_fp: set[str] = set()
        for score, fpath, ctx, v_hash, is_vec in results:
            dedup_key = fpath if fpath else v_hash
            if dedup_key in seen_fp:
                continue
            seen_fp.add(dedup_key)
            # Lấy snippet
            snippet = ""
            try:
                row = conn.execute(
                    "SELECT value FROM dbmem_content WHERE hash=? LIMIT 1", (v_hash,)
                ).fetchone()
                if row:
                    val = row[0]
                    idx = val.lower().find(query.lower())
                    if idx >= 0:
                        s = max(0, idx - 60)
                        e = min(len(val), idx + len(query) + 60)
                        snippet = val[s:e].replace("\n", " ").strip()
            except Exception:
                pass
            merged.append((score, fpath, ctx, snippet, is_vec))

        merged.sort(key=lambda x: (-x[0], not x[4]))

    except Exception as e:
        return f"❌ Lỗi database search: {e}\n\n```\n{traceback.format_exc()}\n```"
    finally:
        try:
            sa.close_main_db()
        except Exception:
            pass

    # ── Format output ──────────────────────────────────────────────
    if not merged:
        return f"🔍 **Kết quả:** `{query}`\n\n_(Không tìm thấy kết quả phù hợp)_"

    lines = [f"## 🔍 Kết quả: `{query}`\n"]
    for i, (score, fpath, ctx, snippet, is_vec) in enumerate(merged[:limit], 1):
        tag = "🟢 Vector" if is_vec else "🟡 Keyword"
        rel = _stored_path(fpath) if fpath else "?"
        lines.append(f"### {i}. `{rel}`")
        lines.append(f"- **Điểm**: `{score:.4f}` | **Loại**: {tag} | **Context**: `{ctx}`")
        if snippet:
            lines.append(f"- **Snippet**:\n  ```text\n  {snippet}\n  ```")
        lines.append("")

    v_cnt = sum(1 for r in merged[:limit] if r[4])
    k_cnt = sum(1 for r in merged[:limit] if not r[4])
    n_show = min(len(merged), limit)
    lines.append(f"---\n📊 {n_show} kết quả (Vector: {v_cnt}, Keyword: {k_cnt})")

    return "\n".join(lines)


# ─── Helper: format PatternStore results → Markdown ─────────────────────
def _format_patterns(patterns: list[dict]) -> str:
    """Chuyển list pattern dict thành Markdown string."""
    if not patterns:
        return "_(Không tìm thấy VERIFIED_PATTERN nào tương tự)_\n"

    parts = ["## 🧩 VERIFIED_PATTERN — Kết quả tìm kiếm\n"]
    for i, p in enumerate(patterns, 1):
        parts.append(
            f"### {i}. Pattern #{p.get('id', '?')} — `{p.get('error_type', '?')}`"
        )
        parts.append(f"- **Mô tả**: {p.get('error_description', '?')}")
        parts.append(f"- **Điểm tương đồng**: `{p.get('score', 0):.2f}`")
        parts.append(f"- **Đã dùng**: `{p.get('application_count', 0)}` lần")
        parts.append(
            f"- **File**: `{p.get('file_path', '?')}:{p.get('line_start', 0)}-{p.get('line_end', 0)}`"
        )
        fix_diff = p.get("fix_diff", "")
        if fix_diff:
            preview = fix_diff[:2000]
            if len(fix_diff) > 2000:
                preview += "\n  ... (truncated)"
            parts.append(f"- **Cách fix**:\n  ```diff\n  {preview}\n  ```")
        parts.append("")

    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
#  Tool 1: search_memory
# ═══════════════════════════════════════════════════════════════════════════
@mcp.tool(
    description=(
        "Tìm kiếm trong semantic memory (code, docs, notes). "
        "Hỗ trợ hybrid search (vector similarity + keyword) hoặc chỉ FTS5 keyword search. "
        "Kết quả bao gồm file path, context label, và snippet chứa query."
    )
)
def search_memory(
    query: str,
    use_vector: bool = True,
    limit: int = 10,
) -> str:
    """
    Tìm kiếm trong sqlite-memory database.

    Args:
        query: Câu truy vấn (tiếng Việt hoặc tiếng Anh, hỗ trợ LIKE search)
        use_vector: True → hybrid (vector + keyword), False → chỉ keyword
        limit: Số kết quả tối đa (1-50, mặc định 10)

    Returns:
        Markdown-formatted search results
    """
    _ = use_vector  # hybrid search luôn chạy cả 2 mode
    return _search_direct(query, min(limit, 50))


# ═══════════════════════════════════════════════════════════════════════════
#  Tool 2: search_verified_patterns
# ═══════════════════════════════════════════════════════════════════════════
@mcp.tool(
    description=(
        "Tìm kiếm VERIFIED_PATTERN — các mẫu lỗi đã được fix và xác nhận "
        "bởi con người hoặc CI/CD. Dùng token overlap scoring (Jaccard similarity, "
        "không cần embedding models). Kết quả bao gồm error_type, fix_diff, file_path."
    )
)
def search_verified_patterns(
    error_description: str,
    top_k: int = 3,
    min_score: float = 0.15,
    error_type_filter: str = "",
) -> str:
    """
    Tìm kiếm các VERIFIED_PATTERN tương tự với error description.

    Args:
        error_description: Mô tả lỗi hoặc stack trace cần tra cứu
        top_k: Số lượng kết quả trả về (1-20, mặc định 3)
        min_score: Ngưỡng Jaccard similarity tối thiểu (0.0-1.0, mặc định 0.15)
        error_type_filter: Lọc theo loại lỗi (vd: 'race_condition', '' = tất cả)

    Returns:
        Markdown-formatted pattern results with diffs
    """
    if PatternStore is None:
        return "❌ pattern_store.PatternStore không khả dụng (import lỗi)."

    try:
        store = PatternStore()
        results = store.search_similar(
            query=error_description,
            top_k=min(top_k, 20),
            min_score=min_score,
            error_type_filter=error_type_filter or None,
        )
        return _format_patterns(results)
    except Exception as e:
        return f"❌ Lỗi khi search verified patterns: {e}\n\n```\n{traceback.format_exc()}\n```"


# ═══════════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════════
def main():
    """Start MCP server over stdio transport."""
    if sa is None:
        print("!! super_agent.py not found. Cannot start MCP server.", file=sys.stderr)
        sys.exit(1)

    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
