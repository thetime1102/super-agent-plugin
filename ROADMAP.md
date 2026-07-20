# 🚀 Super Agent v3 — Roadmap

> Trạng thái: **Phase 1 ✅ | Phase 2+3 ✅ | Phase 4 ⏳** | Cập nhật: 2026-07-20

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

### Kiến trúc (Merged — Real-time, không cron 00:00)

```
[Git Commit]          [GitHub Push]           [Cron 5 phút]
     │                     │                       │
     ▼                     ▼                       ▼
Post-commit hook    Webhook (Gateway)          Poll git log mới
     │                     │                       │
     └─────────────────────┼───────────────────────┘
                           ▼
              auto-consolidate.py
                           │
              ┌────────────┼────────────┐
              ▼            ▼            ▼
        1. git diff    2. Phân loại   3. Lưu vào
           + index       heuristic       SQLite + alert
              (bugs/config/features)
```

### Trigger Sources

| Source | Delay | Mechanism |
|--------|-------|-----------|
| **Git commit (local)** | Real-time (< 1s) | Post-commit hook → `auto-consolidate.py --source webhook` |
| **GitHub push** | ~5 phút | Cron `super-agent-consolidate` mỗi 300s check git log |
| **Gateway webhook** | Real-time (future) | Webhooks plugin + Cloudflare tunnel |

### Database Schema

```sql
-- File: memory/consolidation.db
CREATE TABLE consolidation (
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

-- Tracking: consolidation_tracking(key, value) — lưu last_processed_sha
```

### Classification Heuristic (không cần LLM API)

| Commit message contains | Category | Severity |
|------------------------|----------|----------|
| `fix`, `bug`, `hotfix`, `crash`, `rollback` | `bug` | `critical` nếu crash/rollback |
| `config`, `env`, `setting`, `migration` | `config` | `warning` nếu migration |
| `feat`, `feature`, `add`, `new`, `create` | `feature` | `info` |
| `docs`, `readme`, `comment` | `feature` | `info` |

### Files

| File | Chức năng |
|------|-----------|
| `auto-consolidate.py` | Consolidation engine: git diff → classify → save |
| `.git/hooks/post-commit` | Gọi auto-consolidate --source webhook sau mỗi commit |
| Cron job | `super-agent-consolidate` mỗi 300s (5 phút) |

### GitHub Webhook Setup (Future — khi Gateway public)

1. Bật webhooks plugin trong `openclaw.json`:
```json5
"plugins": {
  "entries": {
    "webhooks": {
      "enabled": true,
      "config": {
        "routes": {
          "consolidate": {
            "path": "/plugins/webhooks/consolidate",
            "sessionKey": "agent:main:main",
            "secret": { "source": "env", "provider": "default", "id": "SUPER_AGENT_WEBHOOK_SECRET" },
            "description": "GitHub webhook → auto consolidation"
          }
        }
      }
    }
  }
}
```
2. GitHub repo → Settings → Webhooks → Add webhook:
   - Payload URL: `https://<tunnel-url>/plugins/webhooks/consolidate`
   - Content type: `application/json`
   - Secret: `SUPER_AGENT_WEBHOOK_SECRET`
   - Events: `Push`, `Workflow runs`

---

## Phase 4: MCP Server (Future ⏳)

### Mục tiêu
Isolated session có thể gọi `super-agent search` qua MCP thay vì exec shell.

### Ý tưởng

```python
# super_agent_mcp.py — MCP server wrapping super_agent functions
# Session A: "search token budget" → MCP call → result → context
```

### Use cases

- Cron job dùng MCP call thay vì exec PowerShell
- Cross-session memory sharing
- Agent trong Telegram có thể search memory
