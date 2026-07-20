# 🧠 Super Agent — NHAT VI CAKE Auto-Indexing & Code Scanner

> **Semantic memory + Proactive code scanning + Tree-sitter code surgery**
> Powering AI agents to understand, search, and fix code automatically.

<p align="center">
  <img src="https://img.shields.io/badge/python-3.11+-blue" alt="Python" />
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License" />
  <img src="https://img.shields.io/badge/status-stable-brightgreen" alt="Status" />
</p>

---

## 📦 Tool Suite

| Tool | File | Chức năng |
|------|------|-----------|
| **🔍 Super Agent** | `super_agent.py` | Auto-indexing engine + Hybrid Search (Vector + FTS5) |
| **🔬 Code Scanner** | `code-scanner.py` | 3-Layer Proactive Scanner: ESLint → DeepSeek Logic → Graph-RAG |
| **🩺 Code Surgery** | `replace_code_symbol.py` | Tree-sitter AST code replace (byte-level, dry-run) |
| **🧠 Auto Consolidate** | `auto-consolidate.py` | Event-driven memory consolidation (git → classify → save) |
| **🔒 Safe Push** | `safe-push.ps1` | PowerShell-safe git commit+push wrapper |

---

## 🚀 Quick Start

### 1. Super Agent — Semantic Memory

Auto-indexes code changes, supports hybrid search (vector + keyword).

```powershell
# Index codebase
python super_agent.py index src/ --context code:dev

# Hybrid search (vector + FTS5)
python super_agent.py search "auto post worker" --vector

# Git incremental index + file watcher
python super_agent.py git-index
python super_agent.py watch --bg
```

### 2. Code Scanner — 3-Layer Proactive

```powershell
# Scan last commit changes
python code-scanner.py

# Scan specific file
python code-scanner.py --file src/service.ts

# Full project scan
python code-scanner.py --all
```

3 layers:
| Layer | Engine | Phát hiện |
|-------|--------|-----------|
| **1** | ESLint | Syntax + Style errors |
| **2** | DeepSeek CoT | Logic bugs: Race/Memory/Transaction |
| **3** | Graph-RAG | Cross-file impact analysis |

### 3. Code Surgery — Tree-sitter Replace

```powershell
# Find symbol location
python replace_code_symbol.py src/file.ts "myFunction" --find

# Dry-run replace (show diff, no write)
python replace_code_symbol.py src/file.ts "myFunction" --code "new code" --dry-run

# Apply replace (auto backup .bak)
python replace_code_symbol.py src/file.ts "myFunction" --code "new code"
```

### 4. Auto Consolidate

```powershell
python auto-consolidate.py --source webhook --context code:dev
```

---

## 🏗️ Architecture

### Event-Driven Pipeline

```
git commit
    ↓ (async, ~420ms)
code-scanner.py
    ↓
┌─ Layer 1: ESLint --format json (syntax/style)
├─ Layer 2: DeepSeek Chain-of-Thought (logic bugs)
│   └─ graph_reverse_deps() → Graph-RAG (cross-file)
├─ Layer 3: auto-consolidate.py (memory save)
└─ Report → .scan_report.json → OpenClaw session ping
```

### Graph-RAG Flow

```
git diff → changed files (anchor points)
    ↓
resolve_imports() → local imports + exports
    ↓
graph_reverse_deps() → parse graph.json (1804 nodes, 3796 edges)
    ├─ Forward: file calls what
    └─ Reverse: what calls file (max depth=3)
    ↓
build_cross_file_context() → token-safe context
    ↓
DeepSeek CoT prompt → logic bug analysis
```

### Safety Guards

| Guard | Value | Description |
|-------|-------|-------------|
| **MAX_TRACE_DEPTH** | 3 | Ngăn vòng lặp vô tận khi trace dependencies |
| **MAX_CONTEXT_TOKENS** | 8000 | Cắt context → signature-only nếu vượt quá |
| **Post-parse validation** | ✅ | replace_code_symbol reject nếu syntax sai |
| **Auto backup** | ✅ | `.bak` trước mọi write operation |
| **Dry-run mode** | ✅ | Xem diff trước, không chạm file |

---

## 🔬 Live Test: Cross-File Impact Analysis

Kịch bản: Đổi `getSystemSetting` return type từ `string` → `object`

```
Graph-RAG phát hiện:
  3 reverse deps: storage-config, caption-service, auto-post-cron
  6 direct callers: dashboard.service, system-config, api-client...
  
Impact: 6 callers sẽ break type nếu thay đổi return type
Depth=3 guard: chặn trace vượt quá 3 tầng
Token guard: context < 8000 tokens
```

---

## 📂 File Structure

```
📁 super-agent-plugin/
├── super_agent.py            # Auto-indexing engine + Hybrid Search
├── super-agent.ps1           # PowerShell CLI wrapper
├── code-scanner.py           # 3-Layer Proactive Scanner (ESLint + CoT + Graph-RAG)
├── replace_code_symbol.py    # Tree-sitter AST code surgery
├── auto-consolidate.py       # Event-driven memory consolidation
├── safe-push.ps1             # PowerShell-safe git push wrapper
├── ROADMAP.md                # Phase plan
└── README.md                 # This file
```

## 🔗 Related

- [NHAT VI CAKE Ecosystem](https://github.com/thetime1102/nhatvi-ecosystem-dev)
- [sqlite-memory](https://github.com/sqliteai/sqlite-memory)
- [Tree-sitter](https://tree-sitter.github.io/)
