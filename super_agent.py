#!/usr/bin/env python3
"""
super_agent.py — NHAT VI CAKE Super Agent Engine v2
=====================================================
Auto-Indexing Engine for semantic memory (sqlite-memory).

Key features:
  - Custom code-file chunking (works with .ts, .py, .js, etc.)
  - Tracks file→chunk mappings for incremental updates
  - Cách 1: watchdog-based file watcher (auto-index on save)
  - Cách 2: git diff incremental index
  - Search + Status

Usage:
  python super_agent.py index <file|dir>          — Index file(s)
  python super_agent.py watch <dir>               — Start watcher daemon
  python super_agent.py git-index [revision]      — Git incremental
  python super_agent.py search <query> [limit]     — Search memory
  python super_agent.py status                     — Show stats
  python super_agent.py clean                      — Clean stale entries
  python super_agent.py daemon                     — Background watcher
"""

import argparse
import hashlib
import io
import math
import os
import re
import sqlite3
import struct
import subprocess
import sys
import threading
import time
import warnings
from typing import List, Optional, Tuple

warnings.filterwarnings("ignore")

# Force UTF-8 on stdout (fix cp932 on Windows)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

def _out(msg: str):
    """Print with emoji-safe encoding (Windows CP932 workaround)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        safe = msg.encode("utf-8", errors="replace").decode("utf-8")
        print(safe)

# ─── Paths ────────────────────────────────────────────────────────────────
_PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))  # super-agent-plugin/

# Memory DB: relative to OpenClaw workspace root (~/.openclaw/workspace/memory/memory.db)
# Walk up until we find workspace/memory/ or fallback to env/userprofile
MEMORY_DB = None
_candidates = [
    os.path.join(os.environ.get("USERPROFILE", ""), ".openclaw", "workspace", "memory", "memory.db"),
    r"C:\Users\tqv11\.openclaw\workspace\memory\memory.db",
]
for _p in _candidates:
    if os.path.isfile(_p) or os.path.isdir(os.path.dirname(_p)):
        MEMORY_DB = _p
        break
if MEMORY_DB is None:
    MEMORY_DB = _candidates[0]

WORKSPACE = os.path.dirname(os.path.dirname(MEMORY_DB))  # workspace/

APPDATA = os.environ.get("APPDATA", "")
SQLMEM_EXT_DIR = os.path.join(APPDATA, "sqlmem", "extensions")
VECTOR_DLL = os.path.join(SQLMEM_EXT_DIR, "sqlite-vector", "1.0.0", "vector.dll")
MEMORY_DLL = os.path.join(SQLMEM_EXT_DIR, "sqlite-memory", "1.3.5", "memory.dll")

# Supported file extensions for indexing
SUPPORTED_EXT = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".py", ".json", ".css", ".scss", ".html", ".vue",
    ".yml", ".yaml", ".ps1", ".md", ".txt",
}

# Directories to skip during indexing
SKIP_DIRS = {
    "node_modules", ".git", "dist", ".next", "build",
    "__pycache__", ".venv", "venv", ".cache", "coverage",
    ".docker", "docker", "data", ".gitpod",
}

CHUNK_SIZE = 80  # lines per chunk
CHUNK_OVERLAP = 10  # lines of overlap


# ─── SQLite Memory Connection ─────────────────────────────────────────────
_conn_local = threading.local()


def get_db() -> sqlite3.Connection:
    """Get thread-local connection. Creates per-thread connections for thread safety."""
    if hasattr(_conn_local, "conn") and _conn_local.conn is not None:
        return _conn_local.conn
    os.makedirs(os.path.dirname(MEMORY_DB), exist_ok=True)
    conn = sqlite3.connect(MEMORY_DB, timeout=10)
    conn.enable_load_extension(True)
    try:
        conn.load_extension(VECTOR_DLL)
        conn.load_extension(MEMORY_DLL)
    except Exception as e:
        print(f"!! Failed to load sqlite-memory extensions: {e}", file=sys.stderr)
        print(f"   VECTOR_DLL={VECTOR_DLL}", file=sys.stderr)
        print(f"   MEMORY_DLL={MEMORY_DLL}", file=sys.stderr)
        sys.exit(1)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    _init_schema(conn)
    # Model already persisted in DB — first add_text auto-loads it
    _conn_local.conn = conn
    return conn


def close_db():
    """Close the thread-local connection."""
    if hasattr(_conn_local, "conn") and _conn_local.conn is not None:
        try:
            _conn_local.conn.close()
        except Exception:
            pass
        _conn_local.conn = None


def close_main_db():
    """Close main thread connection (used in CLI main())."""
    if hasattr(_conn_local, "conn") and _conn_local.conn is not None:
        try:
            _conn_local.conn.close()
        except Exception:
            pass
        _conn_local.conn = None


def _ensure_model(conn):
    """Model persisted in DB — auto-loads on first use."""
    pass

def _init_schema(conn: sqlite3.Connection):
    """Create tracking tables if they don't exist."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sa_filemap (
            file_path TEXT PRIMARY KEY,
            context TEXT NOT NULL DEFAULT 'workspace',
            content_hash TEXT NOT NULL DEFAULT '',
            file_size INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL DEFAULT (unixepoch())
        );
        CREATE TABLE IF NOT EXISTS sa_chunks (
            file_path TEXT NOT NULL,
            mem_hash TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            context TEXT NOT NULL DEFAULT 'workspace',
            PRIMARY KEY (file_path, mem_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_sa_chunks_file ON sa_chunks(file_path);
        CREATE INDEX IF NOT EXISTS idx_sa_chunks_hash ON sa_chunks(mem_hash);
    """)
    conn.commit()


def close_db():
    """Close the thread-local connection."""
    if hasattr(_conn_local, "conn") and _conn_local.conn is not None:
        try:
            _conn_local.conn.close()
        except Exception:
            pass
    _conn_local.conn = None


# ─── File Hashing ─────────────────────────────────────────────────────────

def file_hash(filepath: str) -> Tuple[str, int]:
    """Compute SHA256 of file content. Returns (hex_digest, file_size)."""
    h = hashlib.sha256()
    size = 0
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


# ─── Text Chunking ────────────────────────────────────────────────────────

def chunk_code(text: str, filepath: str) -> List[Tuple[str, int]]:
    """
    Split code into overlapping chunks with file header.
    Returns list of (chunk_text, seq_number).
    """
    lines = text.split("\n")
    if not lines or (len(lines) == 1 and not lines[0]):
        return []

    header = f"# File: {filepath}\n\n"
    chunks: List[Tuple[str, int]] = []
    total = len(lines)

    # For small files (< CHUNK_SIZE lines), use as-is
    if total <= CHUNK_SIZE:
        chunk_text = header + text
        mid_hash = int(hashlib.md5(chunk_text.encode()).hexdigest()[:8], 16)
        chunks.append((chunk_text, mid_hash))
        return chunks

    # Split into overlapping chunks
    seq = 0
    for start in range(0, total, CHUNK_SIZE - CHUNK_OVERLAP):
        end = min(start + CHUNK_SIZE, total)
        chunk_lines = lines[start:end]
        chunk_text = header + "\n".join(chunk_lines)
        if chunk_text.strip():
            seq_val = hash(f"{filepath}:{start}:{end}")
            chunks.append((chunk_text, seq_val))
            seq += 1
        if end == total:
            break

    return chunks


# ─── Memory Operations ────────────────────────────────────────────────────

def add_to_memory(conn: sqlite3.Connection, text: str, context: str) -> Optional[str]:
    """Add text to sqlite-memory, return its hash."""
    try:
        cur = conn.execute("SELECT memory_add_text(?, ?)", (text, context))
        conn.commit()
        # Find the newly added entry
        cur = conn.execute(
            "SELECT hash FROM dbmem_content WHERE value = ? AND context = ? ORDER BY created_at DESC LIMIT 1",
            (text, context),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        return None
    except Exception as e:
        print(f"⚠️  memory_add_text error: {e}")
        return None


def delete_from_memory(conn: sqlite3.Connection, mem_hash: str) -> bool:
    """Delete a single entry from memory by hash."""
    try:
        conn.execute("SELECT memory_delete(?)", (mem_hash,))
        conn.commit()
        return True
    except Exception as e:
        print(f"⚠️  memory_delete error for {mem_hash}: {e}")
        return False


def _stored_path(filepath: str) -> str:
    """Get workspace-relative path for display."""
    try:
        return os.path.relpath(filepath, WORKSPACE)
    except ValueError:
        return filepath


# ─── File Indexing Engine ─────────────────────────────────────────────────

def index_file(conn: sqlite3.Connection, filepath: str, context: str = "workspace") -> bool:
    """
    Index a single file: remove old chunks, re-chunk, add to memory.

    Returns True if file was (re)indexed, False if skipped.
    """
    relpath = _stored_path(filepath)

    # Check file exists
    if not os.path.isfile(filepath):
        print(f"!! Not a file: {relpath}")
        return False

    # Check extension
    ext = os.path.splitext(filepath)[1].lower()
    if ext not in SUPPORTED_EXT:
        return False

    # Check if unchanged
    cur_hash, cur_size = file_hash(filepath)

    cur = conn.execute(
        "SELECT content_hash, file_size FROM sa_filemap WHERE file_path = ?",
        (filepath,),
    )
    row = cur.fetchone()

    if row:
        old_hash = row["content_hash"]
        old_size = row["file_size"]
        if old_hash == cur_hash and old_size == cur_size:
            return False  # unchanged

    # Remove old chunks for this file
    cur = conn.execute("SELECT mem_hash FROM sa_chunks WHERE file_path = ?", (filepath,))
    old_hashes = [r["mem_hash"] for r in cur.fetchall()]
    for h in old_hashes:
        delete_from_memory(conn, h)
    conn.execute("DELETE FROM sa_chunks WHERE file_path = ?", (filepath,))
    conn.commit()

    # Read and chunk content
    try:
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        print(f"!! Error reading {relpath}: {e}")
        return False

    chunks = chunk_code(text, relpath)
    if not chunks:
        print(f"!! Empty file: {relpath}")
        return False

    # Add each chunk to memory
    for chunk_text, seq in chunks:
        mem_hash = add_to_memory(conn, chunk_text, context)
        if mem_hash:
            conn.execute(
                "INSERT OR REPLACE INTO sa_chunks (file_path, mem_hash, seq, context) VALUES (?, ?, ?, ?)",
                (filepath, mem_hash, seq, context),
            )

    # Update filemap
    conn.execute(
        """INSERT OR REPLACE INTO sa_filemap (file_path, context, content_hash, file_size, updated_at)
           VALUES (?, ?, ?, ?, unixepoch())""",
        (filepath, context, cur_hash, cur_size),
    )
    conn.commit()

    print(f"OK Indexed: {relpath} ({len(chunks)} chunks)")
    return True


def index_directory(conn: sqlite3.Connection, dirpath: str, context: str = "workspace"):
    """Recursively index all supported files in a directory."""
    if not os.path.isdir(dirpath):
        print(f"!! Not a directory: {dirpath}")
        return

    count = 0
    for root, dirs, files in os.walk(dirpath):
        # Skip common directories
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        dirs[:] = [d for d in dirs if not d.startswith(".")]

        for fname in files:
            ext = os.path.splitext(fname)[1].lower()
            if ext in SUPPORTED_EXT:
                fpath = os.path.join(root, fname)
                if index_file(conn, fpath, context):
                    count += 1

    print(f"** Indexed {count} files in {dirpath}")


# ─── Search ────────────────────────────────────────────────────────────────

def search(conn: sqlite3.Connection, query: str, limit: int = 10):
    """Search memory using FTS5."""
    print(f"** Search: '{query}'\n")
    try:
        cur = conn.execute(
            """SELECT c.path, c.length, c.context, c.value
               FROM dbmem_content c
               JOIN dbmem_content_source s ON c.path = s.path
               WHERE c.value LIKE ?
               LIMIT ?""",
            (f"%{query}%", limit),
        )
        results = cur.fetchall()
        if not results:
            # Fallback: search without source join
            cur = conn.execute(
                """SELECT path, length, context, value
                   FROM dbmem_content
                   WHERE value LIKE ?
                   LIMIT ?""",
                (f"%{query}%", limit),
            )
            results = cur.fetchall()

        if results:
            for path, length, context, value in results:
                # Find which file this came from
                file_info = ""
                cur2 = conn.execute(
                    "SELECT file_path, seq FROM sa_chunks WHERE mem_hash = ?",
                    (path,),
                )
                chunk_row = cur2.fetchone()
                if chunk_row:
                    file_info = f" [{chunk_row['file_path']}]"

                print(f"  [{context}]{file_info}")
                # Show snippet
                idx = value.lower().find(query.lower())
                if idx >= 0:
                    start = max(0, idx - 80)
                    end = min(len(value), idx + len(query) + 80)
                    snippet = value[start:end].replace("\n", " ")
                    print(f"    ...{snippet}...")
                print()
        else:
            print("  (no results)\n")
    except Exception as e:
        print(f"⚠️  Search error: {e}")



def _cosine_sim(a_bytes: bytes, b_bytes: bytes) -> float:
    dim = len(a_bytes) // 4
    a = struct.unpack(f'{dim}f', a_bytes)
    b = struct.unpack(f'{dim}f', b_bytes)
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na * nb == 0:
        return 0.0
    return dot / (na * nb)


def hybrid_search(conn, query, limit=10, graphify_dir=""):
    results = []
    # Step 1: Vector search
    try:
        conn.execute("SELECT memory_add_text(?, 'sys_srch')", (query,))
        conn.commit()
        cur = conn.execute("SELECT hash FROM dbmem_content WHERE context='sys_srch' LIMIT 1")
        row = cur.fetchone()
        if row:
            cur = conn.execute("SELECT embedding FROM dbmem_vault WHERE hash=?", (row[0],))
            emb = cur.fetchone()
            if emb:
                cur = conn.execute("""
                    SELECT v.hash, v.embedding, c.context
                    FROM dbmem_vault v
                    JOIN dbmem_content c ON v.hash = c.hash
                    WHERE c.context NOT LIKE 'sys_%%'
                """)
                scored = []
                for v_hash, v_emb, ctx in cur.fetchall():
                    sim = _cosine_sim(emb[0], v_emb)
                    scored.append((sim, v_hash, ctx))
                scored.sort(key=lambda x: -x[0])
                for sim, v_hash, ctx in scored[:limit]:
                    cur2 = conn.execute("SELECT file_path FROM sa_chunks WHERE mem_hash=? LIMIT 1", (v_hash,))
                    r2 = cur2.fetchone()
                    fp = r2[0] if r2 else v_hash
                    results.append((sim, fp, ctx, v_hash, True))
            conn.execute("SELECT memory_delete(?)", (row[0],))
            conn.commit()
    except Exception as e:
        print(f"  [vector] Error: {e}")

    # Step 2: Keyword
    try:
        cur = conn.execute(
            "SELECT path, context, value FROM dbmem_content WHERE value LIKE ? AND context NOT LIKE 'sys_%%' LIMIT ?",
            (f"%{query}%", limit))
        for path, ctx, value in cur.fetchall():
            cur2 = conn.execute("SELECT file_path FROM sa_chunks WHERE mem_hash=? LIMIT 1", (path,))
            r2 = cur2.fetchone()
            fp = r2[0] if r2 else path
            results.append((1.0, fp, ctx, path, False))
    except Exception as e:
        print(f"  [keyword] Error: {e}")

    # Merge & dedup
    seen = set()
    merged = []
    for score, fpath, ctx, v_hash, is_vec in results:
        if fpath in seen:
            continue
        seen.add(fpath)
        snippet = ""
        try:
            cur = conn.execute("SELECT value FROM dbmem_content WHERE hash=? LIMIT 1", (v_hash,))
            row = cur.fetchone()
            if row:
                val = row[0]
                idx = val.lower().find(query.lower())
                if idx >= 0:
                    s = max(0, idx - 60)
                    e = min(len(val), idx + len(query) + 60)
                    snippet = val[s:e].replace("\n", " ")
        except Exception:
            pass
        merged.append((score, fpath, ctx, snippet, is_vec))
    merged.sort(key=lambda x: (-x[0], not x[4]))

    if not merged:
        print("  (no results)\n")
        return

    for i, (score, fpath, ctx, snippet, is_vec) in enumerate(merged[:limit]):
        tag = "[V]" if is_vec else "[K]"
        relpath = _stored_path(fpath) if os.path.isabs(fpath) else fpath
        print(f"  {tag} {score:.4f} [{ctx}] {relpath}")
        if snippet:
            print(f"      ...{snippet}...")
        if is_vec and graphify_dir and i == 0:
            _graphify_explain(fpath, graphify_dir)
        print()

    vs = sum(1 for r in results if r[4])
    ks = sum(1 for r in results if not r[4])
    print(f"  --- {len(merged)} results (vector {vs}, keyword {ks})")


def _graphify_explain(file_path, graphify_dir):
    gj = os.path.join(graphify_dir, "graphify-out", "graph.json")
    if not os.path.isfile(gj):
        return
    try:
        rel = _stored_path(file_path)
        print(f"      [graphify] {rel}...")
        r = subprocess.run(["graphify", "explain", file_path, "--graph", gj],
                          capture_output=True, text=True, timeout=15)
        if r.stdout:
            for line in r.stdout.strip().split("\n")[:8]:
                print(f"      | {line}")
    except Exception:
        pass



# ─── Status ────────────────────────────────────────────────────────────────

def show_status(conn: sqlite3.Connection):
    """Show memory DB and tracking tables statistics."""
    # Memory DB stats
    cur = conn.execute("SELECT COUNT(*), COALESCE(SUM(length), 0) FROM dbmem_content")
    chunks, total_bytes = cur.fetchone()
    cur = conn.execute("SELECT COUNT(*) FROM dbmem_vault")
    embeddings = cur.fetchone()[0]

    # Tracking stats
    cur = conn.execute("SELECT COUNT(*) FROM sa_filemap")
    indexed_files = cur.fetchone()[0]
    cur = conn.execute("SELECT COUNT(*) FROM sa_chunks")
    indexed_chunks = cur.fetchone()[0]

    # Get context summary
    cur = conn.execute(
        "SELECT context, COUNT(*) as cnt FROM sa_filemap GROUP BY context ORDER BY cnt DESC"
    )
    contexts = cur.fetchall()

    # Get version
    try:
        cur = conn.execute("SELECT memory_version()")
        ver = cur.fetchone()[0]
    except Exception:
        ver = "unknown"

    print(f"## Super Agent — Memory Status")
    print(f"   Engine: sqlite-memory v{ver}")
    print(f"   DB: {MEMORY_DB}")
    print(f"   Memory chunks: {chunks} ({_fmt_bytes(total_bytes)})")
    print(f"   Embeddings: {embeddings}")
    print(f"   Tracked files: {indexed_files}")
    print(f"   Tracked chunks: {indexed_chunks}")
    print()

    if contexts:
        print("   Contexts:")
        for ctx in contexts:
            print(f"     {ctx['context']}: {ctx['cnt']} files")

    print()

    # Recent files
    cur = conn.execute(
        """SELECT file_path, context, updated_at
           FROM sa_filemap
           ORDER BY updated_at DESC
           LIMIT 8"""
    )
    rows = cur.fetchall()
    if rows:
        print("   Recently indexed:")
        for r in rows:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["updated_at"]))
            print(f"     [{ts}] {r['context']} {r['file_path']}")


def _fmt_bytes(b: int) -> str:
    if b < 1024:
        return f"{b}B"
    elif b < 1024 * 1024:
        return f"{b / 1024:.1f}KB"
    else:
        return f"{b / 1024 / 1024:.1f}MB"


# ─── Clean Stale Entries ──────────────────────────────────────────────────

def clean(conn: sqlite3.Connection):
    """Remove memory entries for files that no longer exist."""
    cur = conn.execute("SELECT file_path FROM sa_filemap")
    rows = cur.fetchall()
    removed = 0

    for r in rows:
        fpath = r["file_path"]
        if not os.path.isfile(fpath):
            # Remove chunks from memory
            cur2 = conn.execute("SELECT mem_hash FROM sa_chunks WHERE file_path = ?", (fpath,))
            for cr in cur2.fetchall():
                delete_from_memory(conn, cr["mem_hash"])
            conn.execute("DELETE FROM sa_chunks WHERE file_path = ?", (fpath,))
            conn.execute("DELETE FROM sa_filemap WHERE file_path = ?", (fpath,))
            conn.commit()
            print(f"-- Cleaned: {fpath}")
            removed += 1

    if removed == 0:
        print("-- No stale entries found")
    else:
        print(f"-- Removed {removed} stale files")


# ─── Cách 2: Git-based Incremental Index ──────────────────────────────────

def git_index(conn: sqlite3.Connection, revision: str = "HEAD", context: str = "workspace"):
    """Index files changed in a git commit (default: HEAD)."""
    # Find git root
    git_dir = _find_git_root(WORKSPACE)
    if not git_dir:
        print("!! No git repository found")
        return

    # Get changed files
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{revision}^..{revision}"],
            capture_output=True, text=True, cwd=git_dir, timeout=30,
        )
        if result.returncode != 0 or not result.stdout.strip():
            # Try HEAD~1..HEAD
            result = subprocess.run(
                ["git", "diff", "--name-only", f"{revision}~1..{revision}"],
                capture_output=True, text=True, cwd=git_dir, timeout=30,
            )
    except Exception as e:
        print(f"!! Git diff failed: {e}")
        return

    files = [f.strip() for f in result.stdout.split("\n") if f.strip()]
    # Also add untracked files that are staged
    try:
        result2 = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True, text=True, cwd=git_dir, timeout=30,
        )
        files += [f.strip() for f in result2.stdout.split("\n") if f.strip()]
    except Exception:
        pass

    if not files:
        print("ii No changed files found")
        return

    # Remove duplicates
    files = list(set(files))

    count = 0
    for relpath in files:
        fpath = os.path.join(git_dir, relpath)
        if os.path.isfile(fpath) and os.path.splitext(fpath)[1].lower() in SUPPORTED_EXT:
            if index_file(conn, fpath, context):
                count += 1

    print(f"** Git-indexed {count}/{len(files)} files")


def _find_git_root(start: str) -> Optional[str]:
    """Find git root directory. Check start and immediate subdirectories."""
    # Check start directory upward
    check = os.path.abspath(start)
    for _ in range(10):
        if os.path.isdir(os.path.join(check, ".git")):
            return check
        parent = os.path.dirname(check)
        if parent == check:
            break
        check = parent

    # Check immediate subdirectories (common: project is workspace/nhatvi-ecosystem-dev)
    # Priority: prefer -dev over prod
    candidates = []
    for entry in os.listdir(start):
        d = os.path.join(start, entry)
        if os.path.isdir(os.path.join(d, ".git")):
            candidates.append(d)

    if not candidates:
        return None

    # Prefer -dev directory over prod
    dev = [c for c in candidates if c.endswith("-dev")]
    if dev:
        return dev[0]
    return candidates[0]


# ─── Cách 1: File Watcher Daemon ──────────────────────────────────────────

def start_watch(watch_dir: str, context: str = "workspace"):
    """Start watchdog file watcher."""
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        print("⚠️  watchdog not installed. Run: pip install watchdog")
        sys.exit(1)

    _watch_lock = threading.Lock()

    class AutoIndexHandler(FileSystemEventHandler):
        def __init__(self, db_path, ctx):
            self.db_path = db_path
            self.context = ctx
            self._debounce = {}

        def _get_conn(self):
            """Create a new thread-local connection."""
            return get_db()

        def _should_index(self, path: str) -> bool:
            # Check extension
            ext = os.path.splitext(path)[1].lower()
            if ext not in SUPPORTED_EXT:
                return False
            # Check skip dirs
            norm = path.replace("\\", "/")
            for skip in SKIP_DIRS:
                if f"/{skip}/" in norm or norm.endswith(f"/{skip}"):
                    return False
            return True

        def _process(self, path: str):
            if not os.path.isfile(path):
                return
            if not self._should_index(path):
                return
            # Debounce: skip if same file was processed <2s ago
            now = time.time()
            last = self._debounce.get(path, 0)
            if now - last < 2.0:
                return
            self._debounce[path] = now

            with _watch_lock:
                conn = self._get_conn()
                try:
                    index_file(conn, path, self.context)
                except Exception as e:
                    print(f"!! Index error: {e}")

        def on_modified(self, event):
            if not event.is_directory:
                self._process(event.src_path)

        def on_created(self, event):
            if not event.is_directory:
                self._process(event.src_path)

    watch_dir = os.path.abspath(watch_dir)
    if not os.path.isdir(watch_dir):
        print(f"!! Directory not found: {watch_dir}")
        sys.exit(1)

    event_handler = AutoIndexHandler(MEMORY_DB, context)
    observer = Observer()
    observer.schedule(event_handler, watch_dir, recursive=True)
    observer.start()
    print(f"== Super Agent watching: {watch_dir}")
    print(f"   Context: {context}")
    print(f"   Supported: {', '.join(sorted(SUPPORTED_EXT))}")
    print(f"   Press Ctrl+C to stop\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        print("\n== Watcher stopped")
    observer.join()


# ─── Daemon Mode ──────────────────────────────────────────────────────────

def daemon_mode():
    """Run watcher as background daemon."""
    dev_dir = os.path.join(WORKSPACE, "nhatvi-ecosystem-dev")
    if os.path.isdir(dev_dir):
        start_watch(dev_dir, "code:dev")
    else:
        start_watch(WORKSPACE)


# ─── CLI ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="## Super Agent — NHAT VI CAKE Semantic Memory Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  super-agent index src/file.ts              Index a single file
  super-agent index src/                     Index directory recursively
  super-agent watch                          Watch nhatvi-ecosystem-dev
  super-agent watch --bg                     Watch in background (detached)
  super-agent git-index                      Index last commit changes
  super-agent search "token budget"          Search memory
  super-agent status                         Show stats
  super-agent clean                          Remove stale entries
  super-agent report-fix                     Record a VERIFIED_PATTERN
  super-agent pattern-stats                  Show pattern learning stats
        """,
    )
    sub = parser.add_subparsers(dest="command")

    # index
    p_idx = sub.add_parser("index", help="Index a file or directory")
    p_idx.add_argument("path", help="File or directory path")
    p_idx.add_argument("--context", default="workspace", help="Memory context label")

    # watch
    p_watch = sub.add_parser("watch", help="Watch directory for changes (Cách 1)")
    p_watch.add_argument("path", nargs="?", help="Directory to watch")
    p_watch.add_argument("--context", default="code:dev", help="Memory context label")
    p_watch.add_argument("--bg", action="store_true", help="Run in background (detached)")

    # git-index
    p_git = sub.add_parser("git-index", help="Index files from git diff (Cách 2)")
    p_git.add_argument("revision", nargs="?", default="HEAD", help="Git revision")
    p_git.add_argument("--context", default="workspace", help="Memory context label")

    # search
    p_search = sub.add_parser("search", help="Search memory")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("limit", nargs="?", type=int, default=10, help="Max results")
    p_search.add_argument("--vector", "-v", action="store_true", help="Use hybrid vector + keyword search")
    p_search.add_argument("--graphify", type=str, nargs="?", const=WORKSPACE, default="",
                          help="Graphify project dir for AST explain (default: WORKSPACE)")

    # status
    sub.add_parser("status", help="Show memory status")

    # clean
    sub.add_parser("clean", help="Remove stale file entries")

    # daemon (internal)
    sub.add_parser("daemon", help="Start background watcher (internal)")

    # report-fix (PatternStore)
    p_rf = sub.add_parser("report-fix", help="Record a VERIFIED_PATTERN for few-shot learning")
    p_rf.add_argument("--type", required=True, help="Error type (race_condition|memory_leak|...)")
    p_rf.add_argument("--description", required=True, help="Error description")
    p_rf.add_argument("--context", default="", help="Error context (scan report, stack trace)")
    p_rf.add_argument("--fix-diff", required=True, help="The fix diff/unified diff")
    p_rf.add_argument("--fix-desc", default="", help="Fix description")
    p_rf.add_argument("--file", default="", help="File path affected")
    p_rf.add_argument("--line-start", type=int, default=0)
    p_rf.add_argument("--line-end", type=int, default=0)
    p_rf.add_argument("--original", default="", help="Original code before fix")
    p_rf.add_argument("--branch", default="dev", help="Git branch")
    p_rf.add_argument("--commit", default="", help="Commit SHA")
    p_rf.add_argument("--approved-by", default="human", choices=["human", "ci-passed"])

    # pattern-stats
    sub.add_parser("pattern-stats", help="Show pattern learning statistics")

    # prune-patterns
    p_prune = sub.add_parser("prune-patterns", help="Keep top N patterns per error type")
    p_prune.add_argument("--keep", type=int, default=5, help="Max patterns per type")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    conn = get_db()

    if args.command == "index":
        path = os.path.abspath(args.path)
        if os.path.isfile(path):
            index_file(conn, path, args.context)
        elif os.path.isdir(path):
            index_directory(conn, path, args.context)
        else:
            print(f"⚠️  Path not found: {path}")

    elif args.command == "watch":
        watch_dir = args.path
        if not watch_dir:
            dev_dir = os.path.join(WORKSPACE, "nhatvi-ecosystem-dev")
            watch_dir = dev_dir if os.path.isdir(dev_dir) else WORKSPACE

        if args.bg:
            # Spawn detached process
            ps_script = os.path.join(
                os.path.dirname(__file__), "super-agent.ps1"
            )
            subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden",
                 "-Command",
                 f"& '{ps_script}' daemon"],
                creationflags=subprocess.CREATE_NO_WINDOW,
                close_fds=True,
            )
            print(f"== Super Agent watcher started in background")
            print(f"   Watching: {watch_dir}")
            print(f"   To stop: taskkill /f /im python.exe (last resort)")
        else:
            start_watch(watch_dir, args.context)

    elif args.command == "git-index":
        git_index(conn, args.revision, args.context)

    elif args.command == "search":
        if args.vector:
            graphify_dir = args.graphify or WORKSPACE
            hybrid_search(conn, args.query, args.limit, graphify_dir)
        else:
            search(conn, args.query, args.limit)

    elif args.command == "status":
        show_status(conn)

    elif args.command == "clean":
        clean(conn)

    elif args.command == "daemon":
        daemon_mode()

    elif args.command == "report-fix":
        try:
            from pattern_store import PatternStore
            store = PatternStore()
            result = store.record_fix(
                error_type=args.type,
                error_description=args.description,
                error_context=args.context,
                fix_diff=args.fix_diff,
                fix_description=args.fix_desc,
                file_path=args.file,
                line_start=args.line_start,
                line_end=args.line_end,
                original_code=args.original,
                branch=args.branch,
                commit_sha=args.commit,
                approved_by=args.approved_by,
            )
            status = "NEW pattern" if result["is_new"] else "UPDATED existing"
            print(f"[PatternStore] {status} #{result['id']} (used {result['application_count']}x)")
        except ImportError:
            print("!! pattern_store.py not found")

    elif args.command == "pattern-stats":
        try:
            from pattern_store import PatternStore
            store = PatternStore()
            stats = store.get_stats()
            print(f"== Pattern Store Statistics ==")
            print(f"  Total patterns: {stats['total_patterns']}")
            print(f"  By type:")
            for t in stats['by_type']:
                print(f"    {t['type']}: {t['count']} patterns, {t['total_applications']} applications")
            print(f"  Most used:")
            for m in stats['most_used']:
                print(f"    \"{m['description']}\" - used {m['times']}x")
        except ImportError:
            print("!! pattern_store.py not found")

    elif args.command == "prune-patterns":
        try:
            from pattern_store import PatternStore
            store = PatternStore()
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
        except ImportError:
            print("!! pattern_store.py not found")

    close_db()


if __name__ == "__main__":
    main()
