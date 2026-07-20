<p align="center">
  <a href="https://nhatvicake.com/">
    <img src="https://raw.githubusercontent.com/thetime1102/super-agent-plugin/main/assets/logo.png" alt="NHAT VI CAKE" width="200" />
  </a>
  <h1 align="center">Super Agent Plugin for OpenClaw</h1>
  <p align="center">
    <em>Powered by <a href="https://nhatvicake.com/">NHAT VI CAKE</a> 🍰</em>
  </p>
  <p align="center">
    <strong>Tree-sitter Repo Mapper + Code Symbol Tool + Context Engine</strong>
    <br />
    Empowering AI agents to understand code structure without reading entire files — <strong>saving up to 98% tokens</strong>.
  </p>
</p>

<p align="center">
  <a href="https://github.com/thetime1102/super-agent-plugin/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License" /></a>
  <a href="https://github.com/thetime1102/super-agent-plugin/releases"><img src="https://img.shields.io/github/v/release/thetime1102/super-agent-plugin" alt="Release" /></a>
  <a href="https://www.npmjs.com/package/@nhatvica/super-agent-plugin"><img src="https://img.shields.io/npm/v/@nhatvica/super-agent-plugin" alt="npm" /></a>
  <a href="https://nodejs.org/"><img src="https://img.shields.io/badge/node-%3E%3D22-brightgreen" alt="Node" /></a>
  <img src="https://img.shields.io/badge/status-stable-green" alt="Status" />
</p>

---

## ✨ Features

### 🧩 `read_code_symbol` Tool
Agent-callable tool to **zoom into a specific function, class, or interface** — returning only the relevant code block instead of the entire file.

### 🧠 `super-agent` Context Engine
Automatically detects when you mention a file path in conversation and injects a **file map** (imports + declarations) into the system prompt context — so the LLM knows the file's structure immediately.

### 🌐 Multi-Language Support
Parses **TypeScript, TSX, JavaScript, Python, JSON, and CSS** with lazy-loaded Tree-sitter WASM grammars — zero native dependencies.

### 🎯 Three Extraction Modes
| Mode | Description | Token Savings |
|------|-------------|---------------|
| `full` | Returns the complete symbol body | — |
| `signature` | Function/class signature only (name + parameters + return type) | ~90-98% |
| `smart` | Full body if < 50 lines, auto-truncated to signature if larger | Adaptive |

### 💾 Smart Memory
- **JSDoc preservation**: Never strips `/** ... */` comments above exported declarations
- **Dependency-aware**: Extracts the full import graph for each file
- **Lazy grammar loading**: Only loads the WASM parser for the file type being accessed

---

## 📦 Installation

### Via OpenClaw CLI
```bash
# Install from local path
openclaw plugins install ./super-agent-plugin

# Or from npm (coming soon)
openclaw plugins install @nhatvica/super-agent-plugin

# Or from ClawHub
openclaw plugins install clawhub:@nhatvica/super-agent-plugin
```

### Via npm (development)
```bash
npm install @nhatvica/super-agent-plugin
```

---

## ⚙️ Configuration

Add to your `openclaw.json`:

```json5
{
  plugins: {
    entries: {
      "super-agent": {
        enabled: true,
        config: {
          // Optional: auto-detected if empty
          projectRoot: "/path/to/your/project",
        },
      },
    },
  },
}
```

The tool is **required by default** — no need to add to `tools.allow`!

### Optional: Custom Project Root
```json5
"super-agent": {
  enabled: true,
  config: {
    projectRoot: "/home/user/projects/my-app",
  },
}
```

---

## 🚀 Usage

### As an Agent (LLM)
When you mention a file path in your message:

```text
User: Fix the error in src/services/llm.service.ts
```

The **Context Engine** automatically injects:
```
📋 FILE MAP: src/services/llm.service.ts
   Size: 16879 bytes, 513 lines

🔗 Imports (8):
   📦 External: axios
   📁 Local: ../utils/logger → getLogger
   📁 Local: ../utils/retry → withRetry

📊 Declarations (10):
   ⚡ callDeepSeek(system, user, options?): Promise<LLMResponse>
   ⚡ callGeminiVision(imageBuffer, prompt): Promise<string>
```

Then the LLM can call the tool to get the full body:

```text
read_code_symbol(filePath="src/services/llm.service.ts", symbolName="callDeepSeek", mode="smart")
```

### Extraction Modes Example

```typescript
// mode: "signature" → 302 chars (98% savings)
async callDeepSeek(
  systemPrompt: string,
  userContent: string,
  options?: { model?: string; temperature?: number; ... }
): Promise<LLMResponse>;

// mode: "full" → 16,879 chars (full function body)
async callDeepSeek(systemPrompt: string, userContent: string, ...) {
  // ... 150 lines of code
}

// mode: "smart" → auto-selects based on body length
```

---

## 🔧 Development

### Prerequisites
- Node.js 22+
- OpenClaw 2026.5.17+

### Setup
```bash
git clone https://github.com/thetime1102/super-agent-plugin.git
cd super-agent-plugin
npm install
```

### Build
```bash
npm run build
```
Compiles TypeScript → `dist/` and copies WASM grammar files.

### Test
```bash
node tests/test-core.mjs           # 45 core tests
node tests/test-integration.mjs    # 26 integration tests with fixtures
```

---

## 🏗️ Architecture

```
super-agent-plugin/
├── src/
│   ├── index.ts                 ← Plugin entry point
│   ├── repo-mapper.ts           ← File analysis + symbol extraction
│   ├── extractor.ts             ← Smart content extraction (3 modes)
│   └── parsers/
│       └── index.ts             ← Multi-language parser registry (lazy-load)
├── scripts/
│   └── copy-wasm.mjs            ← Build-time WASM copy
├── tests/
│   ├── test-core.mjs            ← Core functionality tests
│   ├── test-integration.mjs     ← Integration tests
│   └── fixtures/                ← Sample test files
├── dist/                        ← Output: JS + WASM grammars
│   ├── index.js
│   ├── repo-mapper.js
│   ├── extractor.js
│   ├── parsers/index.js
│   └── *.wasm (x6)
├── openclaw.plugin.json         ← Plugin manifest
└── package.json
```

### Data Flow
```
User mentions file path in message
  │
  ▼ Context Engine assemble()
  ├─ detectFileReferences() → regex + path matching
  ├─ mapFile() → Tree-sitter AST → imports + declarations
  └─ inject systemPromptAddition
  
  ▼ LLM reads file map
  ├─ sees available symbols
  └─ calls read_code_symbol(file, symbol, mode)
  
  ▼ Tool executes
  ├─ Lazy-load grammar (cached after first use)
  ├─ Parse file with Tree-sitter
  └─ Return extracted body (full / signature / smart)
```

### Supported Languages
| Extension | Language | Parser |
|-----------|----------|--------|
| `.ts`, `.mts`, `.cts` | TypeScript | `tree-sitter-typescript` |
| `.tsx`, `.jsx` | TSX / JSX | `tree-sitter-tsx` |
| `.js`, `.mjs`, `.cjs` | JavaScript | Reuses TypeScript parser |
| `.py` | Python | `tree-sitter-python` |
| `.json` | JSON | `tree-sitter-json` |
| `.css` | CSS | `tree-sitter-css` |

---

## 🧪 Test Suite

```
Core Tests (45 tests):
  ✓ Plugin import & registration
  ✓ detectFileReferences (16 path patterns)
  ✓ mapFile + readCodeSymbol parsing
  ✓ Error handling & edge cases
  ✓ Plugin structure validation

Integration Tests (26 tests):
  ✓ Full file analysis with sample fixtures
  ✓ Symbol extraction (function, class, interface, type)
  ✓ Class method detection
  ✓ Empty file handling
```

Run tests:
```bash
node tests/test-core.mjs
node tests/test-integration.mjs
```

---

## 🐛 Known Issues & Bug Tracking

All bugs are tracked on [GitHub Issues](https://github.com/thetime1102/super-agent-plugin/issues):

| Bug | Status | Description |
|-----|--------|-------------|
| #1 | ✅ Fixed | Manifest missing from npm files |
| #2 | ✅ Fixed | WASM_DIR resolve fails in non-dev contexts |
| #3 | ✅ Fixed | Context engine not declared in contracts |
| #4 | ✅ Fixed | No retry on WASM init failure |
| #5 | ✅ Fixed | Root path resolution fails |
| #6 | ✅ Fixed | detectFileReferences regex too narrow |
| #7 | ✅ Fixed | ConfigSchema empty |
| #8 | ✅ Fixed | No Windows backslash support |
| #9 | ✅ Fixed | projectRoot cached in closure |
| #10 | ✅ Fixed | Silent error swallowing |
| #11 | ✅ Fixed | False positive bare word matches |

---

## 📄 License

MIT © [Nhat Vi Cake Team](https://github.com/thetime1102)

---

<p align="center">
  Built with ❤️ for the OpenClaw ecosystem
  <br />
  <a href="https://github.com/thetime1102/super-agent-plugin/issues">Report Bug</a>
  ·
  <a href="https://github.com/thetime1102/super-agent-plugin/issues">Request Feature</a>
</p>
