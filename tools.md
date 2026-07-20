# 🧠 Super Agent — NHAT VI CAKE Auto-Indexing Engine

**Super Agent** là công cụ semantic memory và auto-indexing cho NHAT VI CAKE ecosystem. Tự động chunk code → local embedding → lưu vào sqlite-memory khi file thay đổi.

## Requirements

- Python 3.11+
- SQLite (`sqlite-memory` + `sqlite-vector` extensions)
- Model: [`nomic-embed-text-v1.5.Q8_0.gguf`](https://huggingface.co/nomic-ai/nomic-embed-text-v1.5-GGUF) (~139MB)
- Watchdog: `pip install watchdog`

## Install

```powershell
# Cài extensions + model (đã có sẵn trên máy dev)
%APPDATA%\sqlmem\extensions\sqlite-vector\1.0.0\vector.dll
%APPDATA%\sqlmem\extensions\sqlite-memory\1.3.5\memory.dll
%LOCALAPPDATA%\sqlmem-models\nomic-embed-text-v1.5.Q8_0.gguf

# Cài watchdog cho file watcher
pip install watchdog
```

## Usage

### 🔥 Cách 1: File Watcher (auto-index khi save)

```powershell
# Foreground
python super_agent.py watch

# Background (chạy ngầm)
python super_agent.py daemon
```

Khi bạn save file `.ts`, `.py`, `.js`, `.json`, `.md` → tự động chunk → embedding → lưu vào memory. Debounce 2 giây, skip `node_modules`, `.git`, `dist`.

### 🔥 Cách 2: Git Incremental (auto-index theo commit)

```powershell
python super_agent.py git-index           # Index commit cuối
python super_agent.py git-index HEAD~3    # Index 3 commit gần nhất
```

### Các lệnh khác

```powershell
python super_agent.py index <file|dir>    # Index thủ công
python super_agent.py search "<query>"    # Tra cứu memory (FTS5)
python super_agent.py status              # Stats: chunks, embeddings, files
python super_agent.py clean               # Xoá entries của file đã xoá
```

### Dùng qua PowerShell wrapper

```powershell
.\super-agent.ps1 status
.\super-agent.ps1 search "catalog"
.\super-agent.ps1 watch --bg
```

## Supported File Types

`.ts` `.tsx` `.js` `.jsx` `.mjs` `.cjs` `.py` `.json` `.css` `.scss` `.html` `.vue` `.yml` `.yaml` `.ps1` `.md` `.txt`

## Architecture

```
File Save / Git Commit
       ↓
 Watchdog / post-commit Hook / Cron
       ↓
  super_agent.py index_file()
       ↓
  1. Compute SHA256 hash → compare sa_filemap (skip if unchanged)
  2. Remove old chunks via memory_delete(hash)
  3. Read file, chunk: 80-line overlapping sections + file header
  4. For each chunk: memory_add_text(chunk, context) → local embedding
  5. Update sa_filemap + sa_chunks tracking tables
```

### Memory DB Tables (trong `memory/memory.db`)

| Table | Purpose |
|-------|---------|
| `sa_filemap` | `file_path → content_hash, file_size, updated_at` |
| `sa_chunks` | `file_path → mem_hash, seq, context` |
| `dbmem_content` | sqlite-memory content store |
| `dbmem_vault` | Vector embeddings (FTS5 + vector search) |

### Zero API Cost

Embedding chạy local 100% qua model `nomic-embed-text-v1.5` (llama.cpp backend). Không cần API key.

## File Structure

```
📁 super-agent-plugin/
├── super_agent.py       # Python engine (index, watch, git-index, search, status, clean)
├── super-agent.ps1      # PowerShell CLI wrapper
└── README.md            # This file
```

## Related

- [NHAT VI CAKE Ecosystem](https://github.com/thetime1102/nhatvi-ecosystem-dev)
- [sqlite-memory](https://github.com/sqliteai/sqlite-memory)
- [sqlite-vector](https://github.com/sqliteai/sqlite-vector)
