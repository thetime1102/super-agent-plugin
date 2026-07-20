# 🚀 Super Agent v3 — Roadmap

> Trạng thái: **Phase 1 ✅ | Phase 2+3 ✅ | Phase 4 ✅ | Phase 5 ✅ | Phase 6 ⏳** | Cập nhật: 2026-07-21

---

## Phase 1: Hybrid Search (Vector + AST) ✅ Hoàn thành

### Kiến trúc

```
User Query ("auto post worker crash")
       ↓
  1. Embed query → memory_add_text('query', 'sys_search')
       ↓
  2. cosine_sim(768-dim embedding × 300+ vault entries) → Top 10
       ↓
  3. Lấy file paths từ sa_chunks mapping
       ↓
  4. Graphify explain từng file để lấy function signatures
```

### Commands

```powershell
super-agent search "auto post worker" --vector         # Hybrid search
super-agent search "worker crash" --vector --graphify   # + AST explain
```

### Files

| File | Chức năng |
|------|-----------|
| `super_agent.py` | `hybrid_search()`, `_cosine_sim()`, `_graphify_explain()` |

---

## Phase 2+3: Event-Driven Auto Consolidation ✅ Hoàn thành

### Trigger Sources

| Source | Delay | Mechanism |
|--------|-------|-----------|
| **Git commit (local)** | Real-time (< 1s) | Post-commit hook → `auto-consolidate.py --source webhook` |
| **GitHub push** | ~5 phút | Cron `super-agent-consolidate` mỗi 300s check git log |
| **Gateway webhook** | Real-time | Webhooks plugin + Cloudflare tunnel |

### Files

| File | Chức năng |
|------|-----------|
| `auto-consolidate.py` | Consolidation engine: git diff → classify → save |
| `.git/hooks/post-commit` | Gọi auto-consolidate --source webhook sau mỗi commit |
| Cron job | `super-agent-consolidate` mỗi 300s (5 phút) |

---

## Phase 4: VERIFIED_PATTERN Learning Engine ✅ HOÀN THÀNH (2026-07-21)

### 🧠 Concept: In-Context Learning từ Human Approve

Thay vì fine-tune LLM tốn kém, ta dùng **Few-shot Prompting** với dữ liệu từ những lần anh Vinh approve fix.

### Kiến trúc

```
[Approve/Apply từ anh Vinh]          [Scanner phát hiện lỗi]
        │                                     │
        ▼                                     ▼
  pattern_store.record_fix()        pattern_store.search_similar()
        │                                     │
        ▼                                     ▼
  consolidation.db                  Few-shot prompt injected
  (verified_patterns table)          vào DeepSeek system prompt
        │                                     │
        └─────────────┬───────────────────────┘
                      ▼
          DeepSeek sinh code fix
          CHÍNH XÁC HƠN (dựa trên
          pattern đã verified)
```

### Database Schema (consolidation.db)

```sql
CREATE TABLE verified_patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at INTEGER DEFAULT (unixepoch()),
    error_type TEXT NOT NULL DEFAULT 'unknown',
    error_description TEXT NOT NULL DEFAULT '',
    error_context TEXT NOT NULL DEFAULT '',
    fix_diff TEXT NOT NULL DEFAULT '',
    fix_description TEXT NOT NULL DEFAULT '',
    file_path TEXT NOT NULL DEFAULT '',
    line_start INTEGER DEFAULT 0,
    line_end INTEGER DEFAULT 0,
    original_code TEXT NOT NULL DEFAULT '',
    pattern_hash TEXT NOT NULL UNIQUE,   -- SHA256 của error+fix (dedup)
    application_count INTEGER DEFAULT 1, -- số lần dùng lại pattern
    last_applied INTEGER,                 -- last reuse timestamp
    approved_by TEXT DEFAULT 'human',     -- 'human' | 'ci-passed'
    verified INTEGER DEFAULT 1
);
```

### Files

| File | Chức năng |
|------|-----------|
| `pattern_store.py` | Core module: record, search, few-shot, stats |
| `code-scanner.py` | Tích hợp `search_similar()` trước khi gọi DeepSeek |
| `super_agent.py` | CLI: `report-fix`, `pattern-stats` |
| `scripts/webhook_handler.py` | CI/CD webhook → auto record CI-passed patterns |

### CLI Usage

```powershell
# Record a fix pattern (gọi sau khi approve)
super-agent report-fix --type race_condition --description "..." --fix-diff "..." --file path.ts

# Show learning stats
super-agent pattern-stats

# Search patterns
python pattern_store.py search "worker crash image" --top-k 3
```

### Scoring (Token Overlap - không cần embedding)

- Jaccard similarity trên token sets của error_description
- + error_context weight 0.8
- Threshold: min_score=0.12

---

## Phase 5: CI/CD Webhook Bridge 🏗️ DESIGN DONE (2026-07-21)

### Mục tiêu
Khi GitHub Actions run hoàn tất, webhook báo về local agent:
- **CI Pass** → record `VERIFIED_PATTERN` (approved_by=ci-passed) → tự merge PR
- **CI Fail** → fetch logs → analyze error → auto-fix commit → push

### Architecture

```
[GitHub Actions - Workflow Run Complete]
        │
        ▼
[Cloudflare Tunnel / ngrok] public URL
        │
        ▼
[Webhooks Plugin - Gateway port 18789]
        │
        ▼
[scripts/webhook_handler.py] processing
        │
  ┌─────┴─────┐
  ▼           ▼
CI Pass    CI Fail
  │           │
  ▼           ▼
record     fetch logs
pattern    → analyze
+ merge    → auto-fix
PR         → push commit
```

### Setup Steps

**Step 1: Tunnel**
```powershell
# Cloudflare Tunnel (recommended) hoặc ngrok
cloudflared tunnel --url http://localhost:11999
```

**Step 2: Gateway Plugin Config (openclaw.json)**
```json5
"plugins": {
  "entries": {
    "webhooks": {
      "enabled": true,
      "config": {
        "routes": {
          "ci-webhook": {
            "path": "/webhook/ci-result",
            "sessionKey": "agent:main:main",
            "secret": { "source": "env", "provider": "default", "id": "SUPER_AGENT_WEBHOOK_SECRET" },
            "description": "GitHub Actions webhook → CI/CD webhook handler"
          }
        }
      }
    }
  }
}
```

**Step 3: GitHub Webhook**
- Payload URL: `https://<tunnel-url>/webhook/ci-result`
- Content type: `application/json`
- Secret: `SUPER_AGENT_WEBHOOK_SECRET`
- Events: `Workflow runs`

### Auto-Fix Flow (khi CI fail)

```
CI Fail Webhook
  ↓
webhook_handler.py nhận event
  ↓
GitHub API fetch workflow logs
  ↓
Extract test failure messages
  ↓
code-scanner.py --file failed-file.ts (CoT scan)
  ↓
DeepSeek sinh diff fix (có few-shot từ VERIFIED_PATTERNs)
  ↓
Tạo branch: auto-fix/<error-type>-<hash>
  ↓
Commit + Push
  ↓
GitHub Actions auto-trigger trên branch mới
  ↓
Nếu Pass → tự merge (Phase 5 hoàn chỉnh)
Nếu Fail → lặp lại tối đa 3 lần
```

### Files

| File | Chức năng |
|------|-----------|
| `scripts/webhook_handler.py` | Webhook receiver + processor |

---

## Phase 6: MCP Server (Future ⏳)

### Mục tiêu
Isolated session có thể gọi `super-agent search` và `pattern_store.search` qua MCP thay vì exec shell.

### Ý tưởng

```python
# super_agent_mcp.py — MCP server wrapping super_agent + pattern_store
# Session A: "search token budget" → MCP call → result → context
```

### Use cases

- Cron job dùng MCP call thay vì exec PowerShell
- Cross-session memory sharing
- Agent trong Telegram có thể search memory + patterns
