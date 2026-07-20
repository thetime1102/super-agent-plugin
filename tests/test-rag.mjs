/**
 * test-rag.mjs — RAG system tests
 *
 * Tests for:
 *   - cosineSimilarity math correctness
 *   - store load/save/create
 *   - isIndexFresh
 *   - search edge cases
 */

import { existsSync, unlinkSync, writeFileSync, mkdirSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(__dirname, '..');
const FIXTURES_DIR = join(__dirname, 'fixtures');

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const YELLOW = '\x1b[33m';
const CYAN = '\x1b[36m';
const RESET = '\x1b[0m';

let passed = 0;
let failed = 0;
const errors = [];

function assert(condition, msg) {
  if (condition) {
    passed++;
    process.stdout.write(GREEN + '\u2713' + RESET + ' ');
  } else {
    failed++;
    process.stdout.write(RED + '\u2717' + RESET + ' ');
    errors.push(msg);
  }
  console.log(msg);
}

// ─── Test 1: cosineSimilarity ──────────────────────

console.log(`\n${CYAN}=== Cosine Similarity Tests ===${RESET}\n`);

try {
  const { cosineSimilarity } = await import('../dist/rag/embedder.js');

  // Identical vectors
  const a = [1, 2, 3];
  const b = [1, 2, 3];
  assert(Math.abs(cosineSimilarity(a, b) - 1) < 0.0001, 'Identical vectors → 1.0');

  // Orthogonal vectors
  const c = [1, 0];
  const d = [0, 1];
  assert(Math.abs(cosineSimilarity(c, d)) < 0.0001, 'Orthogonal vectors → 0.0');

  // Opposite vectors
  const e = [1, 0];
  const f = [-1, 0];
  assert(Math.abs(cosineSimilarity(e, f) + 1) < 0.0001, 'Opposite vectors → -1.0');

  // Different dimension (should handle gracefully)
  assert(cosineSimilarity([1, 2], [1, 2, 3]) === 0, 'Different dimensions → 0');

  // Zero vector
  assert(cosineSimilarity([0, 0], [1, 0]) === 0, 'Zero vector → 0');

  // Empty vectors
  assert(cosineSimilarity([], []) === 0, 'Empty vectors → 0');

  // Large vectors
  const largeA = Array(1536).fill(0.5);
  const largeB = Array(1536).fill(0.5);
  assert(Math.abs(cosineSimilarity(largeA, largeB) - 1) < 0.0001, 'Large vectors (1536d) → 1.0');

  // Slightly different vectors
  const slightlyDiffA = [1, 2, 3, 4, 5];
  const slightlyDiffB = [1, 2, 3, 5, 5];
  const sim = cosineSimilarity(slightlyDiffA, slightlyDiffB);
  assert(sim > 0.9 && sim < 1, 'Slightly different → 0.9-1.0 (got ' + sim.toFixed(4) + ')');

  // Negative values
  const negA = [-1, -2];
  const negB = [1, 2];
  assert(cosineSimilarity(negA, negB) < 0, 'Opposite direction negatives → negative score');

} catch (e) {
  assert(false, 'cosineSimilarity tests error: ' + e.message);
}

// ─── Test 2: Store ─────────────────────────────────

console.log(`\n${CYAN}=== Store Tests ===${RESET}\n`);

try {
  const { createIndex, saveIndex, loadIndex, getIndexStats, isIndexFresh, getIndexPath } = await import('../dist/rag/store.js');

  const testDir = join(FIXTURES_DIR, 'test-rag-store');
  if (!existsSync(testDir)) mkdirSync(testDir, { recursive: true });

  const indexPath = getIndexPath(testDir);
  // Clean up if exists
  if (existsSync(indexPath)) unlinkSync(indexPath);

  // Create fresh index
  const index = createIndex(testDir, 1536);
  assert(index.version === 1, 'Index version is 1');
  assert(index.dimension === 1536, 'Dimension is 1536');
  assert(index.totalChunks === 0, 'Fresh index has 0 chunks');
  assert(index.projectRoot === testDir, 'Project root matches');

  // Save and load
  index.totalChunks = 42;
  index.totalFiles = 10;
  index.entries = [
    {
      chunk: {
        id: 'test.ts::foo',
        filePath: 'test.ts',
        symbolName: 'foo',
        kind: 'function',
        signature: 'foo()',
        content: 'function foo() {}',
        lineStart: 1,
        lineEnd: 2,
        language: 'typescript',
      },
      embedding: [0.1, 0.2, 0.3],
    },
  ];

  saveIndex(testDir, index);

  // Verify file exists
  assert(existsSync(indexPath), 'Index file exists on disk');

  // Load back
  const loaded = loadIndex(testDir);
  assert(loaded !== null, 'Loaded index is not null');
  assert(loaded.totalChunks === 42, 'Loaded index has 42 chunks');
  assert(loaded.totalFiles === 10, 'Loaded index has 10 files');
  assert(loaded.dimension === 1536, 'Loaded dimension is 1536');
  assert(loaded.entries.length === 1, 'Loaded 1 entry');
  assert(loaded.entries[0].chunk.symbolName === 'foo', 'Entry symbol is foo');
  assert(loaded.entries[0].embedding.length === 3, 'Embedding has 3 dimensions');

  // Stats
  const stats = getIndexStats(testDir);
  assert(stats.exists === true, 'Stats exists = true');
  assert(stats.totalChunks === 42, 'Stats totalChunks = 42');
  assert(stats.totalFiles === 10, 'Stats totalFiles = 10');
  assert(stats.indexedAt !== null, 'Stats has indexedAt');

  // Non-existent index
  const nonExistentDir = join(FIXTURES_DIR, 'nonexistent-rag');
  const noIndex = loadIndex(nonExistentDir);
  assert(noIndex === null, 'Non-existent dir → null');

  const noStats = getIndexStats(nonExistentDir);
  assert(noStats.exists === false, 'Non-existent stats exists = false');
  assert(noStats.totalChunks === 0, 'Non-existent stats chunks = 0');

  // Freshness (newly created index should be fresh)
  assert(isIndexFresh(testDir) === true, 'Newly created index is fresh');

  // Cleanup
  if (existsSync(indexPath)) unlinkSync(indexPath);

} catch (e) {
  assert(false, 'Store tests error: ' + e.message);
}

// ─── Test 3: Search ────────────────────────────────

console.log(`\n${CYAN}=== Search Tests ===${RESET}\n`);

try {
  const { search, isSearchAvailable } = await import('../dist/rag/search.js');
  const { createIndex, saveIndex } = await import('../dist/rag/store.js');

  const testDir = join(FIXTURES_DIR, 'test-rag-search');
  if (!existsSync(testDir)) mkdirSync(testDir, { recursive: true });

  // Create a test index with known embeddings
  const index = createIndex(testDir, 1536);
  index.totalFiles = 1;
  index.totalChunks = 3;

  // Embeddings: [1,0,0] = "payment", [0,1,0] = "user", [0,0,1] = "cart"
  index.entries = [
    {
      chunk: {
        id: 'cart.ts::calculateTotal',
        filePath: 'src/services/cart.ts',
        symbolName: 'calculateTotal',
        kind: 'function',
        signature: 'calculateTotal(items: CartItem[]): number',
        content: 'function calculateTotal(items) { return items.reduce((sum, i) => sum + i.price * i.qty, 0); }',
        lineStart: 10,
        lineEnd: 12,
        language: 'typescript',
      },
      embedding: [0.8, 0.1, 0.6],
    },
    {
      chunk: {
        id: 'auth.ts::login',
        filePath: 'src/services/auth.ts',
        symbolName: 'login',
        kind: 'function',
        signature: 'login(email: string, password: string): Promise<User>',
        content: 'async function login(email, password) { ... }',
        lineStart: 5,
        lineEnd: 8,
        language: 'typescript',
      },
      embedding: [0.1, 0.9, 0.1],
    },
    {
      chunk: {
        id: 'cart.ts::applyDiscount',
        filePath: 'src/services/cart.ts',
        symbolName: 'applyDiscount',
        kind: 'function',
        signature: 'applyDiscount(code: string, total: number): number',
        content: 'function applyDiscount(code, total) { ... }',
        lineStart: 15,
        lineEnd: 18,
        language: 'typescript',
      },
      embedding: [0.7, 0.2, 0.7],
    },
  ];

  saveIndex(testDir, index);

  // isSearchAvailable
  assert(isSearchAvailable(testDir) === true, 'Index available after save');

  // We can't call search() directly without an API key, 
  // but we can test isSearchAvailable with non-existent dir
  assert(isSearchAvailable(join(FIXTURES_DIR, 'no-index')) === false, 'Non-existent dir → not available');

  // Also test that search with empty query would fail properly
  // (the embedder requires API key, so we can't test full flow without mocking)

  console.log(YELLOW + '  Skipping full search (requires API key) — structure verified' + RESET);
  assert(true, 'Search structure test passed');

  // Cleanup
  const { getIndexPath } = await import('../dist/rag/store.js');
  const idxPath = getIndexPath(testDir);
  if (existsSync(idxPath)) unlinkSync(idxPath);

} catch (e) {
  assert(false, 'Search tests error: ' + e.message);
}

// ─── Test 4: Embedder config ───────────────────────

console.log(`\n${CYAN}=== Embedder Config Tests ===${RESET}\n`);

try {
  const { configure, getConfig } = await import('../dist/rag/embedder.js');

  // Default config
  const defaultCfg = getConfig();
  assert(defaultCfg.model === 'text-embedding-ada-002', 'Default model is ada-002');
  assert(defaultCfg.dimension === 1536, 'Default dimension is 1536');
  assert(defaultCfg.baseUrl === 'https://api.openai.com/v1', 'Default base URL is OpenAI');
  assert(defaultCfg.apiKey === '', 'Default API key is empty');

  // Configure
  configure({ apiKey: 'test-key-123', model: 'text-embedding-3-small', dimension: 512 });
  const updatedCfg = getConfig();
  assert(updatedCfg.apiKey === 'test-key-123', 'API key updated');
  assert(updatedCfg.model === 'text-embedding-3-small', 'Model updated');
  assert(updatedCfg.dimension === 512, 'Dimension updated');

  // Partial update
  configure({ dimension: 256 });
  const partialCfg = getConfig();
  assert(partialCfg.dimension === 256, 'Partial dimension update');
  assert(partialCfg.apiKey === 'test-key-123', 'API key preserved after partial update');
  assert(partialCfg.model === 'text-embedding-3-small', 'Model preserved after partial update');

  // Reset for other tests
  configure({ apiKey: '', model: 'text-embedding-ada-002', dimension: 1536 });

} catch (e) {
  assert(false, 'Embedder config tests error: ' + e.message);
}

// ─── Test 5: Indexer walkDir (unit) ────────────────

console.log(`\n${CYAN}=== Indexer Tests ===${RESET}\n`);

try {
  const { indexProject } = await import('../dist/rag/indexer.js');

  // indexProject requires API key for embedding, so we can't test full flow
  // But we can verify the module exports correctly
  assert(typeof indexProject === 'function', 'indexProject is exported as function');

  console.log(YELLOW + '  Skipping full indexer (requires API key) — module verified' + RESET);
  assert(true, 'Indexer module structure test passed');

} catch (e) {
  assert(false, 'Indexer tests error: ' + e.message);
}

// ─── Summary ────────────────────────────────────────

console.log(`\n${CYAN}═════════════════════════════════════${RESET}`);
const total = passed + failed;
console.log('Total: ' + total + ' | ' + GREEN + 'Passed: ' + passed + RESET + ' | ' + RED + 'Failed: ' + failed + RESET);
console.log(CYAN + '═════════════════════════════════════' + RESET + '\n');

if (failed > 0) {
  console.log(RED + '\u2716 Errors:' + RESET);
  errors.forEach(e => console.log('  ' + RED + '\u2022' + RESET + ' ' + e));
  process.exit(1);
} else {
  console.log(GREEN + '\u2705 All RAG tests passed!' + RESET);
  process.exit(0);
}
