<p align="center">
  <a href="https://nhatvicake.com/">
    <img src="https://raw.githubusercontent.com/thetime1102/super-agent-plugin/main/assets/logo.png" alt="NHAT VI CAKE" width="200" />
  </a>
  <h1 align="center">Super Agent Plugin cho OpenClaw</h1>
  <p align="center">
    <em>Phát triển bởi <a href="https://nhatvicake.com/">NHAT VI CAKE</a> 🍰</em>
  </p>
  <p align="center">
    <strong>Tree-sitter Repo Mapper + Code Symbol Tool + Context Engine</strong>
    <br />
    Giúp AI Agent hiểu cấu trúc code mà không cần đọc cả file — <strong>tiết kiệm đến 98% token</strong>.
  </p>
</p>

<p align="center">
  <a href="https://github.com/thetime1102/super-agent-plugin/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" /></a>
  <a href="https://github.com/thetime1102/super-agent-plugin/releases"><img src="https://img.shields.io/github/v/release/thetime1102/super-agent-plugin" alt="Release" /></a>
  <a href="https://www.npmjs.com/package/@nhatvica/super-agent-plugin"><img src="https://img.shields.io/npm/v/@nhatvica/super-agent-plugin" alt="npm" /></a>
  <a href="https://nodejs.org/"><img src="https://img.shields.io/badge/node-%3E%3D22-brightgreen" alt="Node" /></a>
  <img src="https://img.shields.io/badge/status-stable-green" alt="Status" />
</p>

<p align="center">
  <a href="./README.md">English</a> ·
  <a href="./README.vi.md">Tiếng Việt</a> ·
  <a href="./README.ja.md">日本語</a>
</p>

---

## ✨ Tính năng

### 🧩 Tool `read_code_symbol`
Cho phép AI Agent **zoom vào một function, class hoặc interface cụ thể** — chỉ trả về đoạn code cần thiết thay vì toàn bộ file. Hỗ trợ 3 chế độ:

| Chế độ | Mô tả | Tiết kiệm token |
|---------|-------|-----------------|
| `full` | Trả về toàn bộ body | — |
| `signature` | Chỉ lấy chữ ký hàm (tên + tham số + kiểu trả về) | ~90-98% |
| `smart` | Body đầy đủ nếu < 50 dòng, tự động rút gọn nếu lớn hơn | Tự động |

### 🔎 Tool `semantic_search` (Giai đoạn 2)
**Tìm kiếm code bằng ngôn ngữ tự nhiên.** Sử dụng kiến trúc RAG (Retrieval-Augmented Generation):
- Embed câu truy vấn qua API (text-embedding-ada-002)
- Cơ sở dữ liệu vector đã được index sẵn
- Trả về đường dẫn file, tên symbol và độ tương đồng

### 🧠 Context Engine `super-agent`
Tự động phát hiện khi AI Agent nhắc đến tên file trong hội thoại và **inject file map** (imports + declarations) vào system prompt — giúp LLM hiểu cấu trúc file ngay lập tức.

### 🌐 Hỗ trợ đa ngôn ngữ lập trình
Parse **TypeScript, TSX, JavaScript, Python, JSON, CSS** với Tree-sitter WASM — không cần native binary. Grammar được nạp theo kiểu lazy-load.

---

## 📦 Cài đặt

### Qua OpenClaw CLI
```bash
openclaw plugins install ./super-agent-plugin
# Hoặc sau khi publish lên ClawHub:
openclaw plugins install clawhub:@nhatvica/super-agent-plugin
```

---

## ⚙️ Cấu hình

Thêm vào `openclaw.json`:

```json5
{
  plugins: {
    entries: {
      "super-agent": {
        enabled: true,
        config: {
          // Tùy chọn: đường dẫn project root
          projectRoot: "/path/to/your/project",
          // API key cho semantic_search (OpenAI-compatible)
          embeddingApiKey: "sk-...",
        },
      },
    },
  },
}
```

### Tìm kiếm ngữ nghĩa (Semantic Search)

#### Đánh index project
```bash
EMBEDDING_API_KEY=sk-*** node scripts/reindex.mjs /path/to/your/project
```

#### Sử dụng
```text
semantic_search(query: "hàm xử lý thanh toán Momo", topK: 5)
```

Kết quả gồm: file path, symbol name, phần trăm khớp, gợi ý dùng `read_code_symbol` để xem chi tiết.

---

## 🧪 Kiểm thử

```bash
npm test                 # Chạy tất cả (115 tests)
npm run test:core        # 45 tests core
npm run test:integration # 26 tests integration  
npm run test:rag         # 44 tests RAG
```

---

## 📄 Giấy phép

MIT © [Nhat Vi Cake Team](https://github.com/thetime1102)

---

<p align="center">
  Xây dựng với ❤️ cho hệ sinh thái OpenClaw
  <br />
  <a href="https://github.com/thetime1102/super-agent-plugin/issues">Báo lỗi</a>
  ·
  <a href="https://github.com/thetime1102/super-agent-plugin/issues">Đề xuất</a>
</p>
