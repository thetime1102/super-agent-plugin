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
    AIエージェントがファイル全体を読まずにコード構造を理解 — <strong>最大98%のトークン節約</strong>。
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

## ✨ 機能

### 🧩 `read_code_symbol` ツール
AIエージェントが**特定の関数・クラス・インターフェースにズームイン** — ファイル全体ではなく、関連するコードブロックのみを返します。3つのモードをサポート:

| モード | 説明 | トークン節約 |
|---------|-------|-------------|
| `full` | 完全なボディを返す | — |
| `signature` | 関数シグネチャのみ（名前＋パラメータ＋戻り値） | ~90-98% |
| `smart` | 50行未満なら全文、超えると自動的にシグネチャのみ | 自動適応 |

### 🔎 `semantic_search` ツール (フェーズ2)
**自然言語によるセマンティックコード検索。** RAG (Retrieval-Augmented Generation) アーキテクチャを採用:
- クエリをAPIで埋め込みベクトル化 (text-embedding-ada-002)
- 事前インデックス済みのベクトルデータベースを検索
- ファイルパス、シンボル名、マッチスコアを返却

### 🧠 `super-agent` コンテキストエンジン
会話中にファイルパスが言及されると自動検出し、**ファイルマップ（import + 宣言）をシステムプロンプトに注入** — LLMが即座にファイル構造を把握。

### 🌐 マルチ言語パーサー
**TypeScript, TSX, JavaScript, Python, JSON, CSS** をTree-sitter WASMでパース — ネイティブ依存ゼロ。グラマーは遅延ロード。

---

## 📦 インストール

### OpenClaw CLI
```bash
openclaw plugins install ./super-agent-plugin
# または ClawHub から:
openclaw plugins install clawhub:@nhatvica/super-agent-plugin
```

---

## ⚙️ 設定

`openclaw.json` に追加:

```json5
{
  plugins: {
    entries: {
      "super-agent": {
        enabled: true,
        config: {
          // オプション: プロジェクトルートパス
          projectRoot: "/path/to/your/project",
          // semantic_search 用 API キー (OpenAI互換)
          embeddingApiKey: "sk-...",
        },
      },
    },
  },
}
```

### セマンティック検索

#### プロジェクトのインデックス作成
```bash
EMBEDDING_API_KEY=sk-*** node scripts/reindex.mjs /path/to/your/project
```

#### 使用方法
```text
semantic_search(query: "Momo支払いを処理する関数", topK: 5)
```

結果: ファイルパス、シンボル名、一致率、`read_code_symbol` を使用した詳細表示の提案。

---

## 🧪 テスト

```bash
npm test                 # 全テスト実行 (115 tests)
npm run test:core        # 45 コアテスト
npm run test:integration # 26 結合テスト  
npm run test:rag         # 44 RAGテスト
```

---

## 📄 ライセンス

MIT © [Nhat Vi Cake Team](https://github.com/thetime1102)

---

<p align="center">
  OpenClaw エコシステムのため ❤️ を込めて
  <br />
  <a href="https://github.com/thetime1102/super-agent-plugin/issues">バグ報告</a>
  ·
  <a href="https://github.com/thetime1102/super-agent-plugin/issues">機能リクエスト</a>
</p>
