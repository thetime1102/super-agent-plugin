# 🚀 Super Agent Plugin for OpenClaw

**Tree-sitter Repo Mapper + Code Symbol Tool + Context Engine**  
Giúp OpenClaw Agent hiểu cấu trúc code, đọc symbol mà không cần đọc cả file — tiết kiệm token đáng kể.

---

## ✨ Features

- **`read_code_symbol` Tool** — LLM chủ động gọi để zoom-in vào function/class/interface body
- **`super-agent` Context Engine** — tự động inject file map (imports + declarations) khi user mention file
- **Tree-sitter WASM** — parse TypeScript/TSX AST không cần native binary
- **Dependency-aware** — extract import graph cho mỗi file

## 📦 Installation

```bash
# Via npm (sau khi publish)
openclaw plugins install @nhatvica/super-agent-plugin

# Hoặc từ ClawHub
openclaw plugins install clawhub:@nhatvica/super-agent-plugin
```

## ⚙️ Configuration

Thêm vào `openclaw.json`:

```json5
{
  plugins: {
    slots: {
      contextEngine: "super-agent",
    },
    entries: {
      "super-agent": {
        enabled: true,
      },
    },
  },
  tools: {
    allow: ["read_code_symbol"],
  },
}
```

## 🔧 Development

```bash
# Install
npm install

# Build (tsc + copy wasm)
npm run build

# Validate
npm run plugin:validate

# Test Parse
npm run test:parse
```

## 📁 Project Structure

```
super-agent-plugin/
├── src/
│   ├── index.ts          ← Plugin entry (Context Engine + Tool)
│   └── repo-mapper.ts    ← Core Tree-sitter module
├── scripts/
│   └── copy-wasm.mjs     ← Build-time WASM copy script
├── dist/                  ← Build output + .wasm files
├── openclaw.plugin.json  ← Plugin manifest
└── package.json
```

## 🧠 How it works

```
User: "sửa callDeepSeek trong llm.service.ts"
  │
  ▼ Context Engine assemble()
  ├─ detect file reference → llm.service.ts
  ├─ mapFile() → imports + declarations
  └─ inject context snippet
  
  ▼ LLM sees declarations
  ├─ "callDeepSeek" exists
  └─ calls read_code_symbol("llm.service.ts", "callDeepSeek")
  
  ▼ Tool returns function body
  └─ LLM có code mà không cần đọc cả file (tiết kiệm ~73% token!)
```

## 📄 License

MIT
