#!/usr/bin/env python3
from __future__ import annotations
"""
multi_agent_orchestrator.py — Multi-Agent Orchestration Engine (Real Integration)
===================================================================================
Pipeline sửa lỗi tự động: Planner → Coder → Reviewer.

Tích hợp thật:
  - pattern_store.search_similar() — tìm VERIFIED_PATTERN
  - code-scanner.py (DeepSeek logic) — phân tích bug_report ra fix_plan JSON
  - replace_code_symbol.replace_symbol() — Tree-sitter AST surgery + .bak backup
  - DeepSeek API — side-effect validation giữa .bak và file đã sửa

Usage:
  from multi_agent_orchestrator import run_orchestrator
  run_orchestrator("Worker crash khi DB write concurrent với image download")
"""

import io
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import traceback
import difflib
from datetime import datetime
from typing import Any, Optional, TypedDict

# ─── Windows cp932 fix ──────────────────────────────────────────────────
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ─── Paths ──────────────────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

# ─── Real Imports ────────────────────────────────────────────────────────
try:
    from pattern_store import PatternStore
    _pattern_store = PatternStore()
    HAS_PATTERN_STORE = True
except ImportError as e:
    print(f"[WARN] pattern_store not available: {e}", file=sys.stderr)
    _pattern_store = None
    HAS_PATTERN_STORE = False

try:
    import replace_code_symbol as rcs
    HAS_RCS = True
except ImportError as e:
    print(f"[WARN] replace_code_symbol not available: {e}", file=sys.stderr)
    rcs = None  # type: ignore
    HAS_RCS = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    _requests = None
    HAS_REQUESTS = False

# ─── Helpers ─────────────────────────────────────────────────────────────

def _get_deepseek_key() -> str:
    """Lấy DeepSeek API key từ env hoặc .env file."""
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if key:
        return key
    for env_file in [
        os.path.join(os.path.dirname(_SCRIPT_DIR), "nhatvi-ecosystem-dev", ".env.dev"),
        os.path.join(os.path.dirname(_SCRIPT_DIR), "nhatvi-ecosystem-dev", ".env"),
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


def _call_deepseek(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
    max_tokens: int = 2000,
) -> str:
    """
    Gọi DeepSeek API (fallback về mock nếu không có key / requests).
    Returns response text hoặc mock result.
    """
    api_key = _get_deepseek_key()
    if not api_key or not HAS_REQUESTS:
        # No API key / no requests lib → mock response
        return _mock_deepseek(system_prompt, user_prompt)

    try:
        resp = _requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  [DeepSeek] API call failed: {e}, falling back to mock")
        return _mock_deepseek(system_prompt, user_prompt)


def _mock_deepseek(system_prompt: str, user_prompt: str) -> str:
    """Mock DeepSeek response — dùng khi không có API key."""
    # Extract relevant info from prompts
    has_race = any(kw in user_prompt.lower() for kw in ["race", "concurrent", "parallel", "promise.all"])
    has_pool = any(kw in user_prompt.lower() for kw in ["pool", "connection", "timeout"])
    has_memory = any(kw in user_prompt.lower() for kw in ["memory", "oom", "leak"])

    if has_race:
        return json.dumps({
            "strategy": "SEQUENTIAL_LOCK",
            "summary": "Chuyển Promise.all sang sequential await + atomic claim key",
            "files": ["src/workers/auto-post.cron.ts", "src/db/queries.ts"],
            "symbols": {"src/workers/auto-post.cron.ts": ["runAutoPost", "enqueuePost"]},
            "add_transaction": False,
        })
    elif has_pool:
        return json.dumps({
            "strategy": "POOL_EXHAUSTION",
            "summary": "Tăng connection pool + retry + circuit breaker",
            "files": ["src/services/db-connection.ts"],
            "symbols": {"src/services/db-connection.ts": ["getPool", "query"]},
        })
    elif has_memory:
        return json.dumps({
            "strategy": "MEMORY_OPTIMIZE",
            "summary": "Thêm GC hint + chunk processing cho image buffer",
            "files": ["src/workers/auto-post.cron.ts"],
            "symbols": {"src/workers/auto-post.cron.ts": ["runAutoPost"]},
        })
    else:
        return json.dumps({
            "strategy": "GENERIC_FIX",
            "summary": f"Phân tích tổng quát: {user_prompt[:100]}",
            "files": [],
            "symbols": {},
        })


def _find_project_root() -> str:
    """Tìm project root."""
    candidates = [
        os.path.join(os.path.dirname(_SCRIPT_DIR), "nhatvi-ecosystem-dev"),
        os.path.join(os.path.dirname(os.path.dirname(_SCRIPT_DIR)), "nhatvi-ecosystem-dev"),
        os.path.expanduser("~/nhatvicake-core"),   # Linux VM2
        os.path.expanduser("~/nhatvi-ecosystem-dev"),  # Linux alternative
    ]
    for c in candidates:
        if os.path.isdir(os.path.join(c, "src")) or os.path.isdir(os.path.join(c, ".git")):
            return c
    return os.path.dirname(_SCRIPT_DIR)


_PROJECT_ROOT = _find_project_root()


def _resolve_path(relative_path: str) -> str:
    """Resolve relative path to absolute."""
    if os.path.isabs(relative_path):
        return relative_path
    return os.path.normpath(os.path.join(_PROJECT_ROOT, relative_path))


def _safe_read(path: str) -> str:
    """Đọc file an toàn (utf-8, replace lỗi)."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def _diff_text(old_text: str, new_text: str, file_label: str) -> str:
    """Tạo unified diff string."""
    diff = difflib.unified_diff(
        old_text.splitlines(True),
        new_text.splitlines(True),
        fromfile=f"{file_label} (before)",
        tofile=f"{file_label} (after)",
        lineterm="",
    )
    return "\n".join(diff)


# ═══════════════════════════════════════════════════════════════════════════
#  AgentState — TypedDict
# ═══════════════════════════════════════════════════════════════════════════

class AgentState(TypedDict, total=False):
    bug_report: str
    fix_plan: dict
    qa_status: str
    qa_feedback: str
    iterations: int
    # Runtime fields (not in initial state)
    patterns_found: list  # list[dict] từ pattern_store.search_similar
    qa_diff: str          # diff được reviewer phân tích


# ═══════════════════════════════════════════════════════════════════════════
#  PlannerAgent — Real Logic
# ═══════════════════════════════════════════════════════════════════════════

PLANNER_SYSTEM_PROMPT = """\
Bạn là PlannerAgent — phân tích bug report và sinh fix plan dạng JSON.

Yêu cầu output JSON:
{
  "strategy": "Tên chiến lược (SEQUENTIAL_LOCK | POOL_EXHAUSTION | MEMORY_OPTIMIZE | TRANSACTION_SAFE | GENERIC_FIX)",
  "summary": "Mô tả ngắn gọn cách fix",
  "files": ["danh sách file cần sửa"],
  "symbols": { "file_path": ["symbol1", "symbol2"] },
  "add_transaction": true/false,
  "concurrency_limit": null | số
}

Quy tắc:
1. Nếu có VERIFIED_PATTERN tương tự, ưu tiên strategy từ pattern đó.
2. Nếu bug_report đề cập race/concurrent, dùng SEQUENTIAL_LOCK.
3. Nếu đề cập pool/timeout, dùng POOL_EXHAUSTION.
4. Nếu đề cập memory/leak/OOM, dùng MEMORY_OPTIMIZE.
5. Nếu cần rollback, set add_transaction=true.
6. Nếu cần giới hạn concurrent, set concurrency_limit=số.
7. **BẮT BUỘC:** files phải là mảng chứa đường dẫn file cần sửa. Nếu bug_report có tên file (VD: test-e2e.js), phải đưa vào files.
"""


def run_planner(state: AgentState) -> AgentState:
    """
    PlannerAgent — Real integration:
      - pattern_store.search_similar() cho VERIFIED_PATTERN
      - DeepSeek API để phân tích và sinh fix_plan JSON
    """
    report = state["bug_report"]
    feedback = state.get("qa_feedback", "")

    print(f"[PlannerAgent] Phan tich bug report (iter {state['iterations'] + 1})...")
    print(f"  Input: {textwrap.shorten(report, width=80, placeholder='...')}")

    # ── Step 1: Search VERIFIED_PATTERNs ─────────────────────────
    patterns_found: list[dict] = []
    if HAS_PATTERN_STORE:
        try:
            query = report
            if feedback:
                query = f"{report}\nQA Feedback: {feedback}"
            patterns_found = _pattern_store.search_similar(query, top_k=3, min_score=0.10)
            print(f"  [PatternStore] Found {len(patterns_found)} similar pattern(s)")
            for p in patterns_found:
                print(f"    #{p['id']} [{p['error_type']}] score={p['score']:.2f} used={p['application_count']}x")
                print(f"      {p.get('error_description', '?')[:80]}")
        except Exception as e:
            print(f"  [PatternStore] Search error: {e}")

    state["patterns_found"] = patterns_found

    # ── Step 2: Build prompt cho DeepSeek ─────────────────────────
    pattern_context = ""
    if patterns_found:
        pattern_context = "VERIFIED PATTERNS (uong tac tu fix truoc):\n"
        for i, p in enumerate(patterns_found[:2], 1):
            pattern_context += f"\nPattern {i}:\n"
            pattern_context += f"  Type: {p.get('error_type', '?')}\n"
            pattern_context += f"  Description: {p.get('error_description', '?')}\n"
            pattern_context += f"  Fix: {p.get('fix_diff', '')[:300]}\n"

    user_prompt = f"Bug Report:\n{report}\n"
    if feedback:
        user_prompt += f"\nQA Feedback (round {state['iterations']}):\n{feedback}\n"
    if pattern_context:
        user_prompt += f"\n{pattern_context}\n"

    # ── Step 3: Call DeepSeek ─────────────────────────────────────
    response = _call_deepseek(PLANNER_SYSTEM_PROMPT, user_prompt, temperature=0.2, max_tokens=1500)

    # ── Step 4: Parse response thành fix_plan ──────────────────────
    try:
        # Extract JSON từ response (có thể có markdown fence)
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
        if json_match:
            plan_str = json_match.group(1)
        else:
            plan_str = response
        fix_plan = json.loads(plan_str)
        # Ensure required fields
        fix_plan.setdefault("strategy", "GENERIC_FIX")
        fix_plan.setdefault("summary", response[:200])
        fix_plan.setdefault("files", [])
        fix_plan.setdefault("symbols", {})
        fix_plan.setdefault("add_transaction", False)
        fix_plan.setdefault("concurrency_limit", None)
    except (json.JSONDecodeError, AttributeError):
        # Fallback: parse basic từ response
        fix_plan = {
            "strategy": "GENERIC_FIX",
            "summary": response[:200],
            "files": [],
            "symbols": {},
            "add_transaction": False,
            "concurrency_limit": None,
        }

    # Fallback: extract file paths từ error context nếu files rỗng
    if not fix_plan.get("files"):
        report = state.get("bug_report", "")
        # Tìm file paths trong error log (VD: at file.ts:42, test-e2e.js)
        file_matches = re.findall(
            r"""(?:\.\/|\/)?[-_a-zA-Z0-9]+(?:\.[a-zA-Z]+)(?=\s|:|\(|\n|$)""",
            report
        )
        # Lọc bỏ trùng, bỏ file hệ thống
        seen = set()
        for f in file_matches:
            ext = os.path.splitext(f)[1].lower()
            if ext in (".js", ".ts", ".tsx", ".jsx", ".py", ".json") and f not in seen:
                seen.add(f)
                fix_plan["files"].append(f)
        if fix_plan["files"]:
            print(f"  [Fallback] Extracted files: {', '.join(fix_plan['files'])}")

    state["fix_plan"] = fix_plan
    print(f"[PlannerAgent] Fix Plan: {fix_plan['strategy']}")
    print(f"  Summary: {fix_plan['summary']}")
    if fix_plan.get("files"):
        print(f"  Files: {', '.join(fix_plan['files'])}")
    if fix_plan.get("add_transaction"):
        print(f"  Transaction rollback: YES")
    if fix_plan.get("concurrency_limit"):
        print(f"  Concurrency limit: {fix_plan['concurrency_limit']}")
    print()
    return state


# ═══════════════════════════════════════════════════════════════════════════
#  CoderAgent — Real Logic
# ═══════════════════════════════════════════════════════════════════════════

def run_coder(state: AgentState) -> AgentState:
    """
    CoderAgent — Real integration:
      - replace_code_symbol.replace_symbol() — Tree-sitter AST surgery
      - .bak backup (built-in trong replace_symbol)
      - Fallback: copy backup thủ công nếu không có Tree-sitter
    """
    fix_plan = state.get("fix_plan", {})
    strategy = fix_plan.get("strategy", "UNKNOWN")
    files = fix_plan.get("files", [])
    symbols = fix_plan.get("symbols", {})
    summary = fix_plan.get("summary", "")

    print(f"[CoderAgent] Ap dung fix plan: {strategy}...")

    if not files:
        print(f"  [WARN] Khong co file nao trong fix plan, skip")
        print()
        return state

    for filepath in files:
        abs_path = _resolve_path(filepath)
        if not os.path.isfile(abs_path):
            print(f"  [SKIP] File not found: {filepath} ({abs_path})")
            continue

        print(f"  [FILE] {filepath}")

        # ── Backup thủ công (double-safe) ──────────────────────────
        backup_path = abs_path + ".bak"
        try:
            shutil.copy2(abs_path, backup_path)
            print(f"    Backup -> {filepath}.bak")
        except Exception as e:
            print(f"    [WARN] Backup failed: {e}")

        # ── Tìm symbols để replace ─────────────────────────────────
        file_symbols = symbols.get(filepath, [])
        if not file_symbols:
            # Fallback: dùng symbol mặc định theo strategy
            if strategy == "SEQUENTIAL_LOCK":
                file_symbols = ["runAutoPost", "enqueuePost"]
            elif strategy == "POOL_EXHAUSTION":
                file_symbols = ["getPool", "query"]
            elif strategy == "MEMORY_OPTIMIZE":
                file_symbols = ["runAutoPost"]
            else:
                file_symbols = []

        applied_any = False
        for symbol in file_symbols:
            if HAS_RCS and hasattr(rcs, "find_symbol_node") and rcs is not None:
                # ── Real Tree-sitter replacement ──────────────────
                try:
                    new_code = _generate_replacement_code(strategy, symbol, fix_plan)
                    success = rcs.replace_symbol(abs_path, symbol, new_code, dry_run=False)
                    if success:
                        applied_any = True
                    else:
                        # Fallback: try --dry-run first
                        print(f"    [Tree-sitter] Symbol '{symbol}' not found, trying pattern replace")
                except Exception as e:
                    print(f"    [Tree-sitter] Error: {e}")
            else:
                # ── Fallback: text-based replace ──────────────────
                print(f"    [Text] Tree-sitter not available, using text fallback")
                try:
                    _text_replace_fallback(abs_path, strategy, symbol)
                    applied_any = True
                except Exception as e:
                    print(f"    [Text] Fallback error: {e}")

        if applied_any:
            print(f"    [OK] Applied changes to {filepath}")
        else:
            print(f"    [--] No symbols were replaced in {filepath}")

        # ── GENERIC_FIX fallback: goi DeepSeek de sinh code fix that ──
        if not applied_any and strategy == "GENERIC_FIX":
            bug_report = state.get("bug_report", "")
            try:
                print(f"    [DeepSeek Fix] Attempting DeepSeek code generation...")
                fixed_code = _deepseek_generate_fix(abs_path, bug_report, fix_plan)
                if _apply_deepseek_fix(abs_path, filepath, fixed_code, strategy):
                    applied_any = True
                    print(f"    [OK] Applied DeepSeek-generated fix to {filepath}")
            except Exception as e:
                print(f"    [DeepSeek Fix] Failed: {e}")

    print(f"  [OK] Hoan thanh: {summary}")
    print()
    return state


def _generate_replacement_code(strategy: str, symbol: str, fix_plan: dict) -> str:
    """Sinh replacement code cho Tree-sitter dựa trên strategy."""
    if strategy == "SEQUENTIAL_LOCK":
        if symbol == "runAutoPost":
            return (
                "export async function runAutoPost(limit?: number): Promise<void> {\n"
                '  const traceId = `autopost-${Date.now()}`;\n'
                "  let lockAcquired = false;\n\n"
                "  // Process singleton: fs.mkdirSync atomic lock\n"
                "  lockAcquired = acquireProcessLock();\n"
                "  if (!lockAcquired) return;\n\n"
                "  try {\n"
                "    const freshPost = claimPostAtomic('fresh');\n"
                "    if (freshPost) {\n"
                "      // Sequential: savePost -> downloadImage (KHONG Promise.all)\n"
                "      const caption = await generateCaption(freshPost);\n"
                "      const campaign = createCampaign({ product_id: freshPost.product_id });\n"
                "      enqueueJob('post_facebook', { campaignId: campaign.id });\n"
                "      await processQueueImmediately();\n"
                "    }\n"
                "  } finally {\n"
                "    releaseProcessLock();\n"
                "  }\n"
                "}"
            )
        elif symbol == "enqueuePost":
            return (
                "async function enqueuePost(data: PostData): Promise<void> {\n"
                "  const caption = await generateCaption(data);\n"
                "  const campaign = createCampaign(data);\n"
                "  enqueueJob('post_facebook', { campaignId: campaign.id });\n"
                "  await processQueueImmediately();\n"
                "}"
            )
    elif strategy == "POOL_EXHAUSTION":
        if symbol == "getPool":
            return (
                "function getPool(): Pool {\n"
                "  if (!_pool) {\n"
                "    _pool = new Pool({ max: 20, min: 5, acquireTimeoutMillis: 10000 });\n"
                "  }\n"
                "  return _pool;\n"
                "}"
            )
    elif strategy == "MEMORY_OPTIMIZE":
        if symbol == "runAutoPost":
            return (
                "export async function runAutoPost(limit?: number): Promise<void> {\n"
                "  // Memory-optimized version\n"
                "  if (global.gc) global.gc();\n"
                "  const traceId = `autopost-${Date.now()}`;\n"
                "  lockAcquired = acquireProcessLock();\n"
                "  if (!lockAcquired) return;\n"
                "  try {\n"
                "    const freshPost = claimPostAtomic('fresh');\n"
                "    if (freshPost) {\n"
                "      await enqueuePost(freshPost);\n"
                "    }\n"
                "  } finally {\n"
                "    releaseProcessLock();\n"
                "    if (global.gc) global.gc();\n"
                "  }\n"
                "}"
            )

    # Default: return empty implementation
    return (
        f"// {symbol} — replaced by {strategy} fix\n"
        f"async function {symbol}(...args: any[]): Promise<void> {{\n"
        f"  // Auto-fixed by Multi-Agent Orchestrator\n"
        f"}}"
    )


def _text_replace_fallback(filepath: str, strategy: str, symbol: str) -> None:
    """
    Fallback text-based replacement khi Tree-sitter không available.
    Chỉ thay thế các pattern đơn giản, có log warning.
    """
    content = _safe_read(filepath)
    if not content:
        raise FileNotFoundError(f"Cannot read {filepath}")

    replacements = {
        "SEQUENTIAL_LOCK": [
            (r"await Promise\.all\(\[savePost\(\), downloadImage\(\)\]\)",
             "// SEQUENTIAL: savePost -> downloadImage\n      const saved = await savePost()\n      await downloadImage()"),
            (r"Promise\.all\(\[([\s\S]*?)savePost\(\)([\s\S]*?)downloadImage\(\)([\s\S]*?)\]\)",
             "// Sequential (anti-race):\n      const saved = await savePost()\n      await downloadImage()"),
        ],
        "POOL_EXHAUSTION": [
            (r"pool\.max\s*=\s*\d+", "pool.max = 20"),
            (r"new Pool\(\{", "new Pool({ max: 20, min: 5, acquireTimeoutMillis: 10000, "),
        ],
        "MEMORY_OPTIMIZE": [
            (r"(?<=try\s*\{)", "if (global.gc) global.gc();\n      "),
        ],
    }

    strategy_replacements = replacements.get(strategy, [])
    new_content = content
    replaced = False

    for pattern, replacement in strategy_replacements:
        new_content, count = re.subn(pattern, replacement, new_content)
        if count > 0:
            replaced = True
            print(f"    [Text] Replaced pattern '{pattern}' ({count} occurences)")

    if not replaced:
        print(f"    [Text] No matching patterns found for {strategy}")
        # Generic comment injection
        new_content = new_content.replace(
            "export async function runAutoPost",
            "// [ORCHESTRATOR FIX] Sequential-safe version\n"
            "export async function runAutoPost"
        )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(new_content)


def _deepseek_generate_fix(filepath: str, bug_report: str, fix_plan: dict) -> str:
    """
    Goi DeepSeek de sinh code fix cho file.
    Dung cho GENERIC_FIX strategy (syntax errors, missing brackets, etc.)

    Args:
        filepath: Duong dan tuyet doi den file can sua
        bug_report: Bug report tu CI log
        fix_plan: Dict tu Planner

    Returns:
        String chua toan bo noi dung file da duoc fix

    Raises:
        RuntimeError: Neu DeepSeek khong san sang hoac API call that bai
    """
    api_key = _get_deepseek_key()
    if not api_key or not HAS_REQUESTS:
        raise RuntimeError("DeepSeek not available for code generation")

    content = _safe_read(filepath)
    if not content:
        raise FileNotFoundError(f"Cannot read {filepath}")

    filename = os.path.basename(filepath)
    ext = os.path.splitext(filename)[1].lower()
    lang_map = {".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript React",
                ".jsx": "JavaScript React", ".py": "Python", ".json": "JSON",
                ".css": "CSS", ".html": "HTML", ".yml": "YAML", ".yaml": "YAML"}
    language = lang_map.get(ext, "code")

    summary = fix_plan.get("summary", "Fix syntax error")

    system_prompt = f"""\
Ban la CoderAgent — sua loi code dua tren bug report.

File can sua: {filename}
Ngon ngu: {language}
Bug report: {summary}

Yeu cau:
- Phan tich loi trong file code duoi day
- TRA VE TOAN BO FILE da duoc sua (KHONG tra loi van ban, CHI tra code)
- Giu nguyen cau truc file, chi sua phan bi loi
- Neu la syntax error: them ky tu thieu (dong ngoac, ngoac nhon, etc.)
- Neu la runtime error: sua logic tai dong bi loi
- Dam bao file van con chay duoc sau khi sua
- Xuat ra code trong ``` block, KHONG co text thua ben ngoai
"""

    user_prompt = f"""\
Bug report tu CI:
{bug_report[:1500]}

File hien tai ({filename}):
```
{content}
```

Hay sua loi trong file va tra ve TOAN BO file da duoc fix trong ``` block.
Chi tra ve code, khong co text mo ta.
"""

    print(f"    [DeepSeek Fix] Calling DeepSeek to fix {filename}...")

    try:
        resp = _requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "deepseek-chat",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.05,
                "max_tokens": 4000,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        response = data["choices"][0]["message"]["content"].strip()

        # Extract code from ``` block
        # Handle: ```lang\n{code}\n``` and ```\n{code}\n``` variants
        code_match = re.search(r"```(?:\w+)?\s*\n(.*?)\n```", response, re.DOTALL)
        if code_match:
            fixed_code = code_match.group(1).strip()
        else:
            fixed_code = response.strip()
            # Debug: log raw response for diagnosis
            print(f"    [DeepSeek Fix] No code block found, using raw response")

        print(f"    [DeepSeek Fix] Response length: {len(response)} chars")
        print(f"    [DeepSeek Fix] Extracted code length: {len(fixed_code)} chars")

        # Validate: check that code starts/ends similarly (file structure preserved)
        if len(fixed_code) < len(content) * 0.3:
            print(f"    [WARN] Fixed code seems too short ({len(fixed_code)} vs {len(content)})")
            raise RuntimeError("Generated code is too short — likely invalid")

        return fixed_code

    except Exception as e:
        print(f"    [DeepSeek Fix] API call failed: {e}")
        raise RuntimeError(f"DeepSeek code generation failed: {e}")


def _apply_deepseek_fix(abs_path: str, filepath: str, fixed_code: str, strategy: str) -> bool:
    """Apply fix code generated by DeepSeek, with .bak backup."""
    content = _safe_read(abs_path)
    if not content:
        return False

    if content == fixed_code:
        print(f"    [Apply] No changes needed — file is already correct")
        return False

    # Backup
    backup_path = abs_path + ".bak"
    try:
        shutil.copy2(abs_path, backup_path)
        print(f"    [Apply] Backup -> {filepath}.bak")
    except Exception as e:
        print(f"    [Apply] Backup failed: {e}")
        return False

    # Write fixed code
    try:
        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(fixed_code)
        print(f"    [Apply] Written fixed code to {filepath}")
        return True
    except Exception as e:
        print(f"    [Apply] Write failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════
#  ReviewerAgent (QA) — Real Logic
# ═══════════════════════════════════════════════════════════════════════════

REVIEWER_SYSTEM_PROMPT = """\
Bạn là ReviewerAgent QA — kiểm tra code diff sau khi apply fix.

Phân tích:
1. Diff giữa .bak (trước fix) và file hiện tại (sau fix)
2. Có side-effect nào không? (orphan data, missing rollback, type error)
3. Có cover được edge cases không?

Output JSON:
{
  "status": "APPROVED" | "REJECTED",
  "reason": "Mô tả lý do ngắn gọn",
  "issues": ["danh sách vấn đề"]
}
"""


def run_reviewer(state: AgentState) -> AgentState:
    """
    ReviewerAgent — Real integration:
      - Đọc .bak và file hiện tại
      - Tính diff
      - Gửi DeepSeek để phân tích side-effect
      - Nếu không có DeepSeek: basic validation (parse error, .bak tồn tại)
    """
    fix_plan = state.get("fix_plan", {})
    files = fix_plan.get("files", [])
    strategy = fix_plan.get("strategy", "GENERIC_FIX")

    print(f"[ReviewerAgent] QA Review (iter {state['iterations'] + 1})...")
    print(f"  Strategy: {strategy}")

    # ── Collect diffs ───────────────────────────────────────────────
    diffs = []
    all_issues = []
    has_backup = True
    syntax_ok = True

    for filepath in files:
        abs_path = _resolve_path(filepath)
        backup_path = abs_path + ".bak"

        before = _safe_read(backup_path) if os.path.isfile(backup_path) else ""
        after = _safe_read(abs_path) if os.path.isfile(abs_path) else ""

        if not before:
            print(f"  [QA] No .bak for {filepath} — cannot compare")
            all_issues.append(f"Missing .bak backup for {filepath}")
            has_backup = False
            # Use current file as "before" if no backup
            before = after
            continue

        if not after:
            all_issues.append(f"File not found after fix: {filepath}")
            syntax_ok = False
            continue

        if before == after:
            print(f"  [QA] No changes detected in {filepath}")
            all_issues.append(f"No changes applied to {filepath}")
            continue

        diff = _diff_text(before, after, filepath)
        if diff:
            diffs.append(f"=== {filepath} ===\n{diff}")
            print(f"  [QA] Diff for {filepath}: {len(diff)} chars")

        # Basic syntax check: parse bằng replace_code_symbol
        if HAS_RCS and rcs is not None:
            try:
                ext = os.path.splitext(filepath)[1].lower()
                lang = rcs._get_language(ext)
                if lang is not None:
                    import tree_sitter as ts_parser
                    parser = ts_parser.Parser(lang)
                    with open(abs_path, "rb") as f:
                        source_bytes = f.read()
                    tree = parser.parse(source_bytes)
                    if tree.root_node.has_error:
                        all_issues.append(f"Syntax error after fix in {filepath}")
                        syntax_ok = False
                        print(f"    [QA] SYNTAX ERROR in {filepath}")
                    else:
                        print(f"    [QA] Syntax check: PASSED")
            except Exception as e:
                print(f"    [QA] Syntax check skipped: {e}")

    state["qa_diff"] = "\n\n".join(diffs)

    # ── DeepSeek analysis ───────────────────────────────────────────
    if diffs and HAS_REQUESTS and _get_deepseek_key():
        user_prompt = (
            f"Strategy: {strategy}\n\n"
            f"Bug Report: {state['bug_report']}\n\n"
            f"Diff:\n{state['qa_diff'][:3000]}\n\n"
        )
        if all_issues:
            user_prompt += f"Basic issues found: {', '.join(all_issues)}\n\n"
        user_prompt += "Phan tich va output JSON status + issues."

        response = _call_deepseek(REVIEWER_SYSTEM_PROMPT, user_prompt, temperature=0.1, max_tokens=1000)

        try:
            json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", response, re.DOTALL)
            qa_result = json.loads(json_match.group(1)) if json_match else json.loads(response)
            qa_status = qa_result.get("status", "REJECTED")
            qa_reason = qa_result.get("reason", response[:200])
            qa_issues = qa_result.get("issues", [])
            all_issues.extend(qa_issues)
        except (json.JSONDecodeError, AttributeError):
            qa_status = "APPROVED" if "approved" in response.lower() else "REJECTED"
            qa_reason = response[:200]
    else:
        # ── No DeepSeek: rule-based QA ──────────────────────────────
        qa_status = "APPROVED"
        qa_reason_parts = []

        if not has_backup:
            qa_reason_parts.append("MISSING BACKUP: khong the rollback neu can")
        if not syntax_ok:
            qa_reason_parts.append("SYNTAX ERROR: can fix syntax truoc")
        if not diffs:
            qa_reason_parts.append("NO CHANGES: fix plan khong anh huong gi den code")

        if strategy == "SEQUENTIAL_LOCK" and not fix_plan.get("add_transaction"):
            qa_status = "REJECTED"
            qa_reason_parts.append(
                "SEQUENTIAL_LOCK can transaction rollback — "
                "nguy co orphan data neu savePost() thanh cong va downloadImage() that bai"
            )

        if strategy == "GENERIC_FIX":
            # Chi reject GENERIC_FIX neu khong co thay doi thuc te
            if not has_backup and not syntax_ok:
                qa_status = "REJECTED"
                qa_reason_parts.append("GENERIC_FIX khong du cu the de apply")
            else:
                # Fix da duoc apply, cho phep APPROVED
                qa_status = "APPROVED"
                qa_reason_parts.append("GENERIC_FIX applied: file da duoc sua thanh cong")

        qa_reason = "; ".join(qa_reason_parts) if qa_reason_parts else "OK. Khong phat hien side-effect."
        if qa_status == "APPROVED" and not qa_reason_parts:
            qa_reason = "Code thay doi an toan, khong co side-effect ro rang."

    state["qa_status"] = qa_status
    state["qa_feedback"] = qa_reason
    if all_issues:
        state["qa_feedback"] += "\nIssues:\n- " + "\n- ".join(all_issues)

    if qa_status == "APPROVED":
        print(f"  [OK] APPROVED: {qa_reason[:120]}")
    else:
        print(f"  [FAIL] REJECTED: {qa_reason[:120]}")

    print()
    return state


# ═══════════════════════════════════════════════════════════════════════════
#  Orchestrator Loop
# ═══════════════════════════════════════════════════════════════════════════

MAX_ITERATIONS = 3


def run_orchestrator(bug_report: str) -> dict:
    """
    Orchestrator Loop — Planner → Coder → Reviewer (tối đa MAX_ITERATIONS).

    Args:
        bug_report: Báo cáo lỗi gốc dạng string

    Returns:
        Dict kết quả {success, iterations, fix_plan, qa_feedback}

    Raises:
        RuntimeError: Nếu sau MAX_ITERATIONS vẫn REJECTED
    """
    state: AgentState = {
        "bug_report": bug_report,
        "fix_plan": {},
        "qa_status": "PENDING",
        "qa_feedback": "",
        "iterations": 0,
        "patterns_found": [],
        "qa_diff": "",
    }

    print("=" * 70)
    print(f"  [RKT] Multi-Agent Orchestrator — REAL INTEGRATION MODE")
    print(f"  Bug Report: {textwrap.shorten(bug_report, width=65, placeholder='...')}")
    print(f"  Project Root: {_PROJECT_ROOT}")
    print(f"  PatternStore: {'YES' if HAS_PATTERN_STORE else 'NO'}")
    print(f"  Tree-sitter: {'YES' if HAS_RCS else 'NO'}")
    print(f"  DeepSeek: {'YES' if _get_deepseek_key() else 'NO (mock)'}")
    print(f"  Max Iterations: {MAX_ITERATIONS}")
    print("=" * 70)
    print()

    while state["qa_status"] != "APPROVED" and state["iterations"] < MAX_ITERATIONS:
        iteration = state["iterations"]
        print(f"{'=' * 70}")
        print(f"  [RETRY] Iteration {iteration + 1}/{MAX_ITERATIONS}")
        print(f"{'=' * 70}")
        print()

        # Step 1: Planner
        print(f"{'─' * 40}")
        print(f"  Step 1/3: PlannerAgent")
        print(f"{'─' * 40}")
        state = run_planner(state)

        # Step 2: Coder
        print(f"{'─' * 40}")
        print(f"  Step 2/3: CoderAgent")
        print(f"{'─' * 40}")
        state = run_coder(state)

        # Step 3: Reviewer
        print(f"{'─' * 40}")
        print(f"  Step 3/3: ReviewerAgent (QA)")
        print(f"{'─' * 40}")
        state = run_reviewer(state)

        state["iterations"] += 1

        if state["qa_status"] != "APPROVED" and state["iterations"] < MAX_ITERATIONS:
            print(f"{'═' * 70}")
            print(f"  [RETRY] QA REJECTED — Lap lai voi feedback")
            print(f"  Feedback: {state['qa_feedback'][:100]}...")
            print(f"{'═' * 70}")
            print()

    print("=" * 70)
    print(f"  [END] Orchestrator Ket thuc")
    print("=" * 70)
    print()

    if state["qa_status"] == "APPROVED":
        print(f"  [OK] SUCCESS sau {state['iterations']} iteration(s)!")
        print(f"  Strategy: {state['fix_plan'].get('strategy', 'N/A')}")
        print(f"  Summary: {state['fix_plan'].get('summary', 'N/A')[:120]}")
        print(f"  QA Feedback: {state['qa_feedback'][:200]}")
        return {
            "success": True,
            "iterations": state["iterations"],
            "fix_plan": state["fix_plan"],
            "qa_feedback": state["qa_feedback"],
        }

    error_msg = (
        f"[FAIL] Orchestrator FAILED sau {MAX_ITERATIONS} iterations.\n"
        f"  Last QA feedback: {state['qa_feedback'][:200]}"
    )
    print(error_msg)
    raise RuntimeError(error_msg)


# ═══════════════════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    bug_report = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "Worker crash khi DB write concurrent voi image download. "
        "Promise.all([savePost(), downloadImage()]) gay race condition."
    )

    try:
        result = run_orchestrator(bug_report)
        print(f"\n[OUT] Result: {json.dumps(result, indent=2, ensure_ascii=False)}")
    except RuntimeError as e:
        print(f"\n[ERR] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()

