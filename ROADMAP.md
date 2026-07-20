# 🚀 Super Agent v3 — Roadmap

> Trạng thái: **Phase 1 đang phát triển** | Cập nhật: 2026-07-20

---

## Phase 1: Hybrid Search (Vector + AST) ⏳ Đang làm

### Mục tiêu
Khi có task fix bug, tự động: query → vector search → graphify explain → kết quả chính xác với context.

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
       ↓
  5. Kết hợp: "File X chứa hàm Y liên quan đến query, graphify cho thấy nó gọi Z"
```

### Kỹ thuật

| Component | Chi tiết |
|-----------|----------|
| **Embedding** | `memory_add_text(query, 'sys_search')` → embed → `memory_delete(hash)` cleanup |
| **Similarity** | Python `struct.unpack('768f', blob)` → cosine similarity |
| **File mapping** | `sa_chunks(mem_hash → file_path)` join với vector results |
| **Graphify hook** | `graphify explain <file> --graph graphify-out/graph.json` |
| **Hybrid fallback** | Vector top 5 + FTS keyword top 5 → merge & dedup |

### Files cần sửa

- `super_agent.py`: thêm `hybrid_search()`, cập nhật `search()` command
- `super-agent.ps1`: thêm flag `--vector` / `--hybrid`

### Validation

```powershell
super-agent search "auto post worker facebook" --vector
# Output: top 5 files kèm similarity score + graphify explain
```

---

## Phase 2: Auto Memory Consolidation ⏳ Chờ

### Mục tiêu
Cron 00:00 mỗi ngày: đọc daily notes → LLM phân loại → JSON → SQLite. Không còn ghi tay MEMORY.md.

### Kiến trúc

```
00:00 Cron trigger
       ↓
  1. Đọc memory/YYYY-MM-DD.md (hôm qua)
  2. Đọc sa_filemap (files changed hôm qua)
  3. LLM summarize thành 3 categories:
     - bugs: lỗi đã fix
     - config: cấu hình thay đổi  
     - features: tính năng mới
  4. Lưu vào SQLite bảng memory_consolidation (JSON)
  5. Update MEMORY.md tóm tắt
```

### Database

```sql
CREATE TABLE memory_consolidation (
    date TEXT PRIMARY KEY,
    bugs JSON DEFAULT '[]',
    config_changes JSON DEFAULT '[]',
    features JSON DEFAULT '[]',
    lessons JSON DEFAULT '[]',
    decisions JSON DEFAULT '[]',
    created_at INTEGER DEFAULT (unixepoch())
);
```

### LLM Prompt

```
Từ daily log dưới đây, phân loại thành:
1. Bugs đã fix (mô tả + file + nguyên nhân)
2. Cấu hình thay đổi (key + giá trị cũ + mới)
3. Tính năng mới (mô tả + files affected)
4. Bài học (lesson learned)
5. Quyết định kiến trúc

Log: {daily_notes_content}
```

### Files cần tạo

- `auto-consolidate.py` — cron worker script
- `cron job` — `super-agent-consolidate` daily 00:00

---

## Phase 3: CI/CD Monitor ⏳ Chờ

### Mục tiêu
Git push dev → webhook → tự động git diff + graphify check → cảnh báo lỗi trước deploy.

### Kiến trúc

```
GitHub Push (webhook)
       ↓
  POST /webhook/super-agent (port 18789 or Gateway route)
       ↓
  1. git diff --name-only HEAD~1..HEAD
  2. Với mỗi file thay đổi:
     - graphify explain → check dependencies
     - super-agent search "tên hàm cũ" → detect breaking changes
  3. Nếu phát hiện:
     - API response format thay đổi
     - Function signature thay đổi
     - DB schema thay đổi mà không có migration
  4. Alert qua Telegram
```

### Rules

| Rule | Pattern | Hành động |
|------|---------|-----------|
| API breaking | `res.json({...})` thay đổi key | WARN + suggest frontend update |
| Schema drift | Thêm/xoá column mà không có migration | BLOCK |
| Import missing | File xoá export mà file khác import | BLOCK |
| Config change | `.env` thay đổi | INFO + review |

### Files cần tạo

- `ci-monitor.py` — webhook handler + analyzer
- GitHub webhook config → Gateway route

---

## Phase 4: MCP Server (Future)

### Mục tiêu
Isolated session có thể gọi `super-agent search` qua MCP thay vì exec shell.

### Ý tưởng
```python
# super_agent_mcp.py — MCP server wrapping super_agent functions
# Session A: "search token budget" → MCP call → result → context
```
