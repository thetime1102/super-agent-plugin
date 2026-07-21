#!/usr/bin/env python3
"""
code-scanner.py -- Super Agent Proactive Code Scanner v3 (Chain-of-Thought)
==============================================================================
Layer 2 nang cap: Cross-file Context Tracing truoc khi ket luan.

  Khi phat hien function nghi van:
    1. resolve_imports() -- parse import statements, resolve local files
    2. trace_callers() -- semantic_search + graphify path de tim context
    3. build_cross_file_context() -- tong hop data flow cho LLM
    4. analyze_logic() -- DeepSeek voi Chain-of-Thought prompt

Usage:
  python code-scanner.py                          # Scan git diff (post-commit)
  python code-scanner.py --all                    # Scan toan bo project
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

# --- Paths ---
WORKSPACE = r"C:\Users\tqv11\.openclaw\workspace"
DEV_DIR = os.path.join(WORKSPACE, "nhatvi-ecosystem-dev")
SUPER_AGENT_DIR = os.path.join(WORKSPACE, "super-agent-plugin")
GRAPHIFY_OUT = os.path.join(DEV_DIR, "graphify-out", "graph.json")
REPORT_DIR = os.path.join(WORKSPACE, "memory")
REPORT_FILE = os.path.join(REPORT_DIR, ".scan_report.json")  # Temp, se ghi de boi hash

# --- Pattern Store (Few-shot Learning) ---
sys.path.insert(0, SUPER_AGENT_DIR)
try:
    from pattern_store import PatternStore
    _pattern_store = PatternStore()
    HAS_PATTERN_STORE = True
except ImportError:
    _pattern_store = None
    HAS_PATTERN_STORE = False

# --- Safety Guards ---
MAX_TRACE_DEPTH = 3
MAX_CONTEXT_TOKENS = 8000  # Neu vuot qua, chi lay function signatures


# --- DeepSeek API Key ---
def _get_deepseek_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    for env_file in [
        os.path.join(DEV_DIR, ".env.dev"),
        os.path.join(DEV_DIR, ".env"),
    ]:
        if os.path.isfile(env_file):
            with open(env_file, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("DEEPSEEK_API_KEY"):
                        key = line.split("=", 1)[1].strip().strip("'\"")
                        if key:
                            return key
    return ""


# ==========================================================================
# CROSS-FILE CONTEXT TRACING (Chain-of-Thought)
# ==========================================================================

def resolve_imports(file_path: str) -> list[dict]:
    """
    Parse import statements tu file, resolve local file paths.
    Returns list of {module, is_local, resolved_path, exported_symbols}
    """
    results = []
    if not os.path.isfile(file_path):
        return results

    file_dir = os.path.dirname(os.path.realpath(file_path))
    ext = os.path.splitext(file_path)[1]

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return results

    # Match import statements
    patterns = [
        r"""import\s+{\s*([^}]+)\s*}\s+from\s+['"]([^'"]+)['"]""",  # import { x } from 'y'
        r"""import\s+(\w+)\s+from\s+['"]([^'"]+)['"]""",            # import x from 'y'
        r"""import\s+['"]([^'"]+)['"]""",                            # import 'x'
        r"""from\s+['"]([^'"]+)['"]\s+import\s+{\s*([^}]+)\s*}""",  # from 'y' import { x }
    ]

    for pat in patterns:
        for m in re.finditer(pat, content):
            is_local = False
            resolved_path = ""
            symbols = []

            if pat.startswith("from"):
                module = m.group(1)
                symbols = [s.strip() for s in m.group(2).split(",")] if m.lastindex >= 2 else []
            elif pat == patterns[0]:
                symbols = [s.strip() for s in m.group(1).split(",")]
                module = m.group(2)
            elif pat == patterns[1]:
                symbols = [m.group(1).strip()]
                module = m.group(2)
            else:
                module = m.group(1)
                symbols = []

            # Check if local path
            if module.startswith(".") or module.startswith("/"):
                is_local = True
                # Resolve path
                candidate = os.path.join(file_dir, module)
                if not candidate.endswith(ext):
                    for try_ext in [ext, ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"]:
                        p = candidate + try_ext if not candidate.endswith(try_ext) else candidate
                        if os.path.isfile(p):
                            resolved_path = os.path.normpath(p)
                            break

            results.append({
                "module": module,
                "is_local": is_local,
                "resolved_path": resolved_path,
                "exported_symbols": symbols,
            })

    return results


def get_exported_functions(file_path: str) -> list[str]:
    """Lay danh sach exported function names tu file (bang regex)."""
    funcs = []
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        # export function / export async function / export const fn =
        for m in re.finditer(r"export\s+(?:async\s+)?function\s+(\w+)", content):
            funcs.append(m.group(1))
        for m in re.finditer(r"export\s+(?:const|let|var)\s+(\w+)\s*[=:]\s*(?:async\s+)?(?:\(|function)", content):
            funcs.append(m.group(1))
    except Exception:
        pass
    return funcs


def trace_callers(file_path: str, function_name: str) -> list[dict]:
    """Search cho callers cua function bang semantic_search + graphify."""
    callers = []

    # Method 1: Graphify path
    if os.path.isfile(GRAPHIFY_OUT):
        try:
            r = subprocess.run(
                ["graphify", "path", function_name, "--graph", GRAPHIFY_OUT],
                capture_output=True, text=True, timeout=15,
            )
            if r.stdout.strip():
                callers.append({"source": "graphify", "info": r.stdout.strip()[:500]})
        except Exception:
            pass

    # Method 2: Semantic search
    super_agent_py = os.path.join(SUPER_AGENT_DIR, "super_agent.py")
    if os.path.isfile(super_agent_py):
        try:
            r = subprocess.run(
                [sys.executable, super_agent_py, "search", function_name, "5"],
                capture_output=True, text=True, timeout=30,
            )
            if r.stdout.strip():
                callers.append({"source": "semantic_search", "info": r.stdout.strip()[:500]})
        except Exception:
            pass

    return callers


def _estimate_tokens(text: str) -> int:
    """Uoc luong token count (4 chars ~ 1 token)."""
    return len(text) // 4


def _truncate_context(context: str) -> str:
    """Token Limit Guard: Cat context neu vuot qua MAX_CONTEXT_TOKENS."""
    tokens = _estimate_tokens(context)
    if tokens <= MAX_CONTEXT_TOKENS:
        return context
    # Chi lay signature lines (ngan hon), bo body code
    lines = context.split("\n")
    sig_lines = [l for l in lines if any(k in l for k in [
        "FILE:", "LOCAL IMPORTS:", "GRAPH-RAG:", "GRAPHIFY NODE:",
        "export", "function", "interface", "type ", "class ",
        "reverse_deps", "forward_deps", "depends on", "affected by",
    ])]
    truncated = "\n".join(sig_lines)
    truncated += f"\n\n[CONTEXT TRUNCATED: {tokens} tokens > {MAX_CONTEXT_TOKENS} max. Showing signatures only.]"
    return truncated


def graph_reverse_deps(changed_files: list[str], depth: int = 0) -> dict:
    """
    Graph-RAG: Doc graph.json, trace reverse dependencies toi da MAX_TRACE_DEPTH.
    Tra ve: { changed_file: {forward_deps, reverse_deps} }
    """
    if depth >= MAX_TRACE_DEPTH:
        return {}
    result = {}
    if not os.path.isfile(GRAPHIFY_OUT):
        return result

    try:
        with open(GRAPHIFY_OUT, "r", encoding="utf-8") as f:
            graph = json.load(f)
    except Exception:
        return result

    nodes = graph.get("nodes", [])
    links = graph.get("links", graph.get("edges", []))

    # Build node lookup: file name -> node id
    file_to_node = {}
    for n in nodes:
        nid = n.get("id", "")
        nfile = n.get("file", n.get("label", ""))
        if nfile and nid:
            key = os.path.basename(nfile).replace(".ts", "").replace(".js", "")
            file_to_node[key] = nid

    # Build reverse dep map: target_id -> [source_ids]
    reverse = {}
    forward = {}
    for link in links:
        src = link.get("source", "")
        tgt = link.get("target", "")
        rel = link.get("relation", link.get("type", ""))
        if not src or not tgt:
            continue
        if tgt not in reverse:
            reverse[tgt] = []
        reverse[tgt].append({"source": src, "relation": rel})
        if src not in forward:
            forward[src] = []
        forward[src].append({"target": tgt, "relation": rel})

    # For each changed file, find affected chain
    for cf in changed_files:
        base = os.path.basename(cf).replace(".ts", "").replace(".js", "").replace(".tsx", "")
        node_id = file_to_node.get(base, "")
        if not node_id:
            continue

        # Forward: what does this file import/call
        deps = forward.get(node_id, [])
        # Reverse: what imports/calls this file
        affected = reverse.get(node_id, [])

        result[cf] = {
            "forward_deps": [d["target"] for d in deps[:8]],
            "reverse_deps": [d["source"] for d in affected[:8]],
            "relations": [d["relation"] for d in (deps + affected)[:10]],
        }

    return result


def build_cross_file_context(changed_files: list[str]) -> str:
    """
    Xay dung cross-file context cho LLM (GRAPH-RAG):
    1. Imports cua tung file changed
    2. Export functions cua cac file local duoc import
    3. Graphify explain + reverse deps (Graph-RAG)
    """
    parts = []
    seen_files = set()

    # Bat dau voi Graph-RAG reverse deps
    graph_rag = graph_reverse_deps(changed_files)
    if graph_rag:
        parts.append("=== GRAPH-RAG: REVERSE DEPENDENCY CHAIN ===")
        for cf, info in graph_rag.items():
            rel = os.path.basename(cf)
            if info["reverse_deps"]:
                parts.append(f"  [{rel}] affected by -> {', '.join(info['reverse_deps'][:5])}")
            if info["forward_deps"]:
                parts.append(f"  [{rel}] depends on -> {', '.join(info['forward_deps'][:5])}")

    for file_path in changed_files:
        if not os.path.isfile(file_path):
            continue

        rel_path = os.path.relpath(file_path, DEV_DIR)
        parts.append(f"\n=== FILE: {rel_path} ===")

        # Imports
        imports = resolve_imports(file_path)
        local_imports = [i for i in imports if i["is_local"]]

        if local_imports:
            parts.append("LOCAL IMPORTS:")
            for imp in local_imports:
                parts.append(f"  - {imp['module']} -> {imp['resolved_path']}")
                parts.append(f"    symbols: {', '.join(imp['exported_symbols'][:5])}")
                if imp["resolved_path"] and imp["resolved_path"] not in seen_files:
                    seen_files.add(imp["resolved_path"])
                    exports = get_exported_functions(imp["resolved_path"])
                    if exports:
                        parts.append(f"    exports: {', '.join(exports[:8])}")

        # Graphify explain + chain
        if os.path.isfile(GRAPHIFY_OUT):
            try:
                rel_short = os.path.relpath(file_path, DEV_DIR)
                r = subprocess.run(
                    ["graphify", "explain", rel_short, "--graph", GRAPHIFY_OUT],
                    capture_output=True, text=True, timeout=15,
                    cwd=DEV_DIR,
                )
                if r.stdout:
                    lines = r.stdout.strip().split("\n")[:6]
                    parts.append("GRAPHIFY NODE:")
                    for line in lines:
                        parts.append(f"  {line}")
            except Exception:
                pass

    return "\n".join(parts)


# ==========================================================================
# LAYER 1: ESLint
# ==========================================================================

def run_eslint(files: list[str]) -> dict:
    """Chay ESLint --format json. CHI SCAN, KHONG FIX."""
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
    except subprocess.TimeoutExpired:
        print("   [eslint] Timeout")
    except Exception as e:
        print(f"   [eslint] {e}")

    return results


# ==========================================================================
# LAYER 2: DeepSeek Chain-of-Thought Logic Scan
# ==========================================================================

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
            ["git", "diff", f"{base}..{current}", "--", "*.ts", "*.tsx", "*.js", "*.mjs", "*.cjs"],
            capture_output=True, timeout=30, cwd=git_dir,
        )
        return r.stdout.decode("utf-8", errors="replace")
    except Exception:
        return ""


def get_changed_files() -> list[str]:
    """Lay danh sach file changed trong commit cuoi."""
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1..HEAD", "--", "*.ts", "*.tsx", "*.js", "*.mjs", "*.cjs", "*.js"],
            capture_output=True, timeout=15, cwd=DEV_DIR,
        )
        out = r.stdout.decode("utf-8", errors="replace")
        files = [os.path.join(DEV_DIR, f.strip()) for f in out.split("\n") if f.strip()]
        return files
    except Exception:
        return []


def call_deepseek(prompt: str, api_key: str) -> str:
    """Call DeepSeek API."""
    import urllib.request, urllib.error

    payload = json.dumps({
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": (
                "You are a senior TypeScript code reviewer. Use Chain-of-Thought reasoning. "
                "Analyze cross-file context for LOGIC BUGS only. "
                "Focus on: race conditions, memory leaks, missing transactions, async safety."
            )},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
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
        with urllib.request.urlopen(req, timeout=45) as resp:
            result = json.loads(resp.read())
            return result["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return f"!! DeepSeek API {e.code}: {e.read().decode()[:200]}"
    except Exception as e:
        return f"!! DeepSeek error: {e}"


def analyze_logic_with_context(diff_text: str, changed_files: list[str], api_key: str) -> list[dict]:
    """
    Chain-of-Thought Logic Scan:
      1. Doc git diff
      2. resolve_imports cho tung file changed
      3. trace_callers cho function phuc tap
      4. Build cross-file context
      5. Gui cho DeepSeek phan tich
    """
    if not diff_text.strip() or not api_key:
        return []

    # Step 1-4: Build context + Token Limit Guard
    print("   [COT] Building cross-file context...")
    cross_context = build_cross_file_context(changed_files)
    context_summary = _truncate_context(cross_context)
    raw_tokens = _estimate_tokens(cross_context)
    kept_tokens = _estimate_tokens(context_summary)
    print(f"   [COT] Context: {raw_tokens} tokens -> truncated to {kept_tokens} (max {MAX_CONTEXT_TOKENS})")

    # Step 4.5: Few-shot Learning — search VERIFIED_PATTERNs tuong tu
    few_shot_prompt = ""
    if HAS_PATTERN_STORE and _pattern_store:
        try:
            # Build query tu diff + file names
            query_parts = [os.path.basename(f) for f in changed_files[:3]]
            query_parts.append(diff_text[:500])
            query = " ".join(query_parts)
            patterns = _pattern_store.search_similar(query, top_k=3, min_score=0.12)
            if patterns:
                few_shot_prompt = _pattern_store.build_few_shot_examples(patterns)
                print(f"   [Few-shot] Found {len(patterns)} VERIFIED_PATTERNs from past fixes")
            else:
                print("   [Few-shot] No similar patterns found")
        except Exception as e:
            print(f"   [Few-shot] Error: {e}")

    # Step 5: Chain-of-Thought prompt
    prompt = f"""You are analyzing a code diff with cross-file context.

{few_shot_prompt}

DIFF (the actual changes):
```typescript
{diff_text[:4000]}
```

CROSS-FILE CONTEXT (imports, dependencies, graphify):
```
{context_summary}
```

Use Chain-of-Thought to analyze for LOGIC BUGS ONLY:

1. FIRST: Understand the data flow across files (who calls who)
2. THEN: Check for:
   - RACE CONDITION: Concurrent DB writes? Shared state without lock?
   - MEMORY LEAK: Missing cleanup in React/Vue? setInterval without clear?
   - MISSING TRANSACTION: Multi-step DB writes without transaction?
   - ASYNC SAFETY: Promise.all on dependent ops? Missing await?

Respond JSON array ONLY:
[{{"type":"race_condition|memory_leak|missing_transaction|async_safety",
   "severity":"critical|warning",
   "file":"path.ts",
   "line":42,
   "description":"Brief description",
   "suggestion":"Fix suggestion",
   "cot_reasoning":"Your chain-of-thought reasoning"}}]
If clean, respond: []"""

    print("   [COT] Sending to DeepSeek...")
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


# ==========================================================================
# REPORT
# ==========================================================================

def _get_latest_report_file() -> str:
    """Tim file report moi nhat (theo commit hash hoac timestamp)."""
    dir_path = REPORT_DIR
    if not os.path.isdir(dir_path):
        return REPORT_FILE
    files = [f for f in os.listdir(dir_path) if f.startswith(".scan_report_") and f.endswith(".json")]
    if not files:
        return REPORT_FILE
    # Sort by timestamp trong ten file (format: .scan_report_<sha>.json)
    files.sort(reverse=True)
    return os.path.join(dir_path, files[0])


def _cleanup_old_reports():
    """Xoa report files cu hon 24h."""
    dir_path = REPORT_DIR
    if not os.path.isdir(dir_path):
        return
    now = time.time()
    for fname in os.listdir(dir_path):
        if fname.startswith(".scan_report_") and fname.endswith(".json"):
            fpath = os.path.join(dir_path, fname)
            try:
                age = now - os.path.getmtime(fpath)
                if age > 86400:  # 24 hours
                    os.unlink(fpath)
            except Exception:
                pass


def write_report(eslint: dict, logic: list[dict], files_scanned: list[str]):
    """Ghi scan report voi commit hash trong ten file."""
    critical_logic = [i for i in logic if i.get("severity") == "critical"]

    # Lay commit hash
    commit_hash = "unknown"
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, timeout=10, cwd=DEV_DIR,
        )
        commit_hash = r.stdout.decode("utf-8", errors="replace").strip() or "unknown"
    except Exception:
        pass

    report = {
        "commit_hash": commit_hash,
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

    # File name with commit hash (chong overwrite)
    report_file = os.path.join(REPORT_DIR, f".scan_report_{commit_hash}.json")
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    # Clean old reports > 24h
    _cleanup_old_reports()

    return report


def print_report(report: dict):
    commit_hash = report.get("commit_hash", "??")
    print(f"\n## Scan Report [{commit_hash}]:")
    print(f"   Files: {report['files_scanned'][:3]}")
    print(f"   ESLint: {report['eslint']['errors']} err / {report['eslint']['warnings']} warn")
    print(f"   Logic: {report['logic']['total']} issues ({report['logic']['critical']} critical)")
    if report["has_issues"]:
        print(f"\n!! Issues found! Report: .scan_report_{commit_hash}.json")


# ==========================================================================
# MAIN
# ==========================================================================

def scan(scan_all: bool = False, single_file: str = ""):
    """Main scan pipeline: ESLint -> CoT Logic -> Report. KHONG auto-fix."""
    print("## Code Scanner v3 -- Chain-of-Thought Logic Scan\n")

    # Step 0: Determine files
    files = []
    if single_file:
        files = [single_file]
    elif scan_all:
        files = ["src/", "web/src/"]
    else:
        r = subprocess.run(
            ["git", "diff", "--name-only", "HEAD~1..HEAD", "--", "*.ts", "*.tsx", "*.js", "*.mjs", "*.cjs", "*.js"],
            capture_output=True, timeout=15, cwd=DEV_DIR,
        )
        files = [f.strip() for f in r.stdout.decode("utf-8", errors="replace").split("\n") if f.strip()]
        if not files:
            print("   No changed files. Nothing to scan.")
            return

    changed_full = [os.path.join(DEV_DIR, f) if not os.path.isabs(f) else f for f in files]
    print(f"   Scanning {len(files)} file(s)...")

    # Layer 1: ESLint
    print("\n## [Layer 1] ESLint...")
    eslint = run_eslint(files)
    print(f"   Errors: {len(eslint['errors'])} | Warnings: {len(eslint['warnings'])}")
    for e in eslint["errors"][:3]:
        rel = os.path.relpath(e["file"], DEV_DIR) if os.path.isabs(e["file"]) else e["file"]
        print(f"     ERR {rel}:{e['line']}  {e['rule']} - {e['message'][:80]}")

    # Layer 2: CoT Logic Scan
    print("\n## [Layer 2] DeepSeek Chain-of-Thought Logic Scan...")
    api_key = _get_deepseek_key()

    if not api_key:
        print("   !! DEEPSEEK_API_KEY not found. Check .env.dev or env vars.")
        print("   Layer 2 skipped.")
        logic = []
    else:
        print(f"   API Key loaded ({api_key[:8]}...{api_key[-4:]})")
        diff = get_git_diff_text(DEV_DIR)

        if diff.strip() and changed_full:
            logic = analyze_logic_with_context(diff, changed_full, api_key)
            if logic:
                print(f"   Found {len(logic)} potential logic issue(s):")
                for issue in logic:
                    sev = "[CRIT]" if issue.get("severity") == "critical" else "[WARN]"
                    print(f"     {sev} [{issue.get('type','?')}] {issue.get('file','?')}:{issue.get('line','?')}")
                    print(f"        {issue.get('description','')}")
                    cot = issue.get('cot_reasoning', '')
                    if cot:
                        print(f"        [COT] {cot[:150]}...")
            else:
                print("   No logic issues found [OK]")
        else:
            print("   No diff to analyze")
            logic = []

    # Generate report
    report = write_report(eslint, logic if api_key else [], files)
    print_report(report)

    # Context Pruning: push findings to consolidation.db
    if logic and api_key:
        try:
            import sqlite3
            CONS_DB = os.path.join(WORKSPACE, "memory", "consolidation.db")
            conn = sqlite3.connect(CONS_DB)
            now = datetime.now().strftime("%Y-%m-%d")
            for issue in logic:
                conn.execute(
                    """INSERT INTO consolidation (date, category, title, description, files_affected, severity, source, commit_sha)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (now, issue.get("type", "bug"),
                     issue.get("description", "")[:120],
                     issue.get("cot_reasoning", issue.get("description", "")),
                     json.dumps(files[:5]),
                     issue.get("severity", "info"),
                     "scanner_v3",
                     "")
                )
            conn.commit()
            conn.close()
            print(f"   [Prune] {len(logic)} findings saved to consolidation.db")
        except Exception as e:
            print(f"   [Prune] Error: {e}")

    if report["has_issues"]:
        print(f"\n!! Issues detected. Reported to .scan_report.json + consolidation.db")
    else:
        print(f"\n[OK] Clean scan - no issues found.")


def main():
    parser = argparse.ArgumentParser(description="Super Agent Code Scanner v3 - CoT Logic Scan")
    parser.add_argument("--all", action="store_true", help="Scan toan bo project")
    parser.add_argument("--file", type=str, help="Scan 1 file cu the")
    args = parser.parse_args()
    scan(scan_all=args.all, single_file=args.file)


def check_report():
    """Doc va xoa report file moi nhat - goi tu OpenClaw session."""
    report_file = _get_latest_report_file()
    if os.path.isfile(report_file):
        with open(report_file, "r", encoding="utf-8") as f:
            report = json.load(f)
        os.unlink(report_file)
        return report
    return None


if __name__ == "__main__":
    main()
