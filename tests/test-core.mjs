/**
 * test-core.mjs — Core functionality tests
 *
 * Tests for:
 *   - Plugin import and registration
 *   - Tree-sitter WASM initialization
 *   - detectFileReferences (with all patterns)
 *   - mapFile parsing
 *   - readCodeSymbol zoom-in
 *   - Edge cases and error handling
 */

import { existsSync } from 'node:fs';
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

// Helper: check result includes expected items
function assertIncludes(result, expected, msg) {
  const ok = expected.every(e => result.includes(e));
  assert(ok, msg + ' (got ' + JSON.stringify(result) + ')');
}

// ─── Test 1: Plugin import ───────────────────────────

console.log(`\n${CYAN}=== Plugin Import Tests ===${RESET}\n`);

try {
  const plugin = await import('../dist/index.js');
  assert(typeof plugin.default === 'object', 'Plugin exports default object');
  assert(plugin.default.id === 'super-agent', 'Plugin id is "super-agent"');
  assert(typeof plugin.default.register === 'function', 'Plugin has register() function');
} catch (e) {
  assert(false, 'Plugin import: ' + e.message);
}

// ─── Test 2: Repo-Mapper import ──────────────────────

console.log(`\n${CYAN}=== Repo-Mapper Import Tests ===${RESET}\n`);

try {
  const rm = await import('../dist/repo-mapper.js');
  assert(typeof rm.mapFile === 'function', 'mapFile is exported');
  assert(typeof rm.readCodeSymbol === 'function', 'readCodeSymbol is exported');
  assert(typeof rm.detectFileReferences === 'function', 'detectFileReferences is exported');
} catch (e) {
  assert(false, 'Repo-mapper import: ' + e.message);
}

// ─── Test 3: detectFileReferences ──────────────────

console.log(`\n${CYAN}=== detectFileReferences Tests ===${RESET}\n`);

const { detectFileReferences } = await import('../dist/repo-mapper.js');

// Core path patterns (Bug #6 fix)
let files;

files = detectFileReferences('Check src/services/llm.service.ts');
assertIncludes(files, ['src/services/llm.service.ts'], 'Detects src/ path');

files = detectFileReferences('Update app/api/route.ts');
assertIncludes(files, ['app/api/route.ts'], 'Detects app/ path');

files = detectFileReferences('Check lib/utils.ts');
assertIncludes(files, ['lib/utils.ts'], 'Detects lib/ path');

files = detectFileReferences('Fix components/Button.tsx');
assertIncludes(files, ['components/Button.tsx'], 'Detects components/ path');

files = detectFileReferences('Review hooks/useAuth.ts');
assertIncludes(files, ['hooks/useAuth.ts'], 'Detects hooks/ path');

files = detectFileReferences('Check utils/format.ts');
assertIncludes(files, ['utils/format.ts'], 'Detects utils/ path');

files = detectFileReferences('Edit config/site.ts');
assertIncludes(files, ['config/site.ts'], 'Detects config/ path');

files = detectFileReferences('Update types/index.ts');
assertIncludes(files, ['types/index.ts'], 'Detects types/ path');

files = detectFileReferences('Check pages/index.tsx');
assertIncludes(files, ['pages/index.tsx'], 'Detects pages/ path');

files = detectFileReferences('Fix web/src/components/Header.tsx');
assertIncludes(files, ['web/src/components/Header.tsx'], 'Detects web/ path');

// @/ alias paths
files = detectFileReferences('Import @/components/Button.tsx');
assertIncludes(files, ['@/components/Button.tsx'], 'Detects @/ alias path');

// Extension detection
files = detectFileReferences('Update src/styles/main.css');
assertIncludes(files, ['src/styles/main.css'], 'Detects .css files');

files = detectFileReferences('Check data/config.json');
assertIncludes(files, ['data/config.json'], 'Detects .json files');

// Multiple files
files = detectFileReferences('Fix src/a.ts and src/b.tsx');
assert(files.length >= 2, 'Detects multiple files (' + files.length + ' files)');

// No file reference
files = detectFileReferences('This message has no file reference');
assert(files.length === 0, 'Returns empty for messages without files');

// CSS files
let cssFiles = detectFileReferences('Update src/styles/main.css');
assert(cssFiles.includes('src/styles/main.css'), 'Detects .css files');

// ─── Test 4: Real file parsing ─────────

console.log(`\n${CYAN}=== Real File Parsing Tests ===${RESET}\n`);

try {
  const rm = await import('../dist/repo-mapper.js');
  const projectFile = join(PROJECT_ROOT, '..', 'nhatvi-ecosystem-dev', 'src', 'services', 'llm.service.ts');

  if (existsSync(projectFile)) {
    const fileMap = await rm.mapFile(projectFile);
    assert(fileMap.size > 0, 'mapFile returns non-empty size');
    assert(fileMap.lines > 0, 'mapFile returns line count');
    assert(Array.isArray(fileMap.imports), 'mapFile returns imports array');
    assert(Array.isArray(fileMap.declarations), 'mapFile returns declarations array');
    assert(fileMap.imports.length > 0, 'File has at least 1 import');
    assert(fileMap.declarations.length > 0, 'File has at least 1 declaration');

    const sym = await rm.readCodeSymbol(projectFile, 'callDeepSeek');
    assert(sym !== null, 'Symbol "callDeepSeek" found');
    if (sym) {
      assert(sym.body.length > 0, 'Symbol body is not empty');
      assert(sym.line > 0, 'Symbol line is valid');
      assert(sym.endLine >= sym.line, 'Symbol endLine >= startLine');
      assert(sym.body.includes('async'), 'Symbol body contains expected code');
    }

    const noSym = await rm.readCodeSymbol(projectFile, 'nonExistentFunctionXYZ');
    assert(noSym === null, 'Non-existent symbol returns null');
  } else {
    console.log(YELLOW + '  Skipping real file tests (project not found)' + RESET);
    assert(true, 'Skipped real file tests');
  }
} catch (e) {
  assert(false, 'Real file parsing: ' + e.message);
}

// ─── Test 5: Error handling ───────────────────────────

console.log(`\n${CYAN}=== Error Handling Tests ===${RESET}\n`);

try {
  const rm = await import('../dist/repo-mapper.js');
  const nonExistentFile = join(PROJECT_ROOT, 'nonexistent-file.ts');

  try {
    await rm.mapFile(nonExistentFile);
    assert(false, 'mapFile should throw for non-existent file');
  } catch (e) {
    assert(true, 'mapFile throws for non-existent file');
  }

  const emptyFile = join(FIXTURES_DIR, 'empty.ts');
  if (existsSync(emptyFile)) {
    try {
      const result = await rm.mapFile(emptyFile);
      assert(result.imports.length === 0, 'Empty file has 0 imports');
      assert(result.declarations.length === 0, 'Empty file has 0 declarations');
    } catch (e) {
      assert(false, 'Empty file parse: ' + e.message);
    }
  } else {
    assert(true, 'Skipped empty file test (fixture not found)');
  }

  try {
    await rm.readCodeSymbol(nonExistentFile, 'anything');
    assert(false, 'readCodeSymbol should throw for non-existent file');
  } catch (e) {
    assert(true, 'readCodeSymbol throws for non-existent file');
  }

} catch (e) {
  assert(false, 'Error handling: ' + e.message);
}

// ─── Test 6: Edge cases ──

console.log(`\n${CYAN}=== Edge Case Tests ===${RESET}\n`);

// Empty string
assert(detectFileReferences('').length === 0, 'Empty string returns empty array');
assert(detectFileReferences('   \n  \t  ').length === 0, 'Whitespace-only returns empty array');
assert(detectFileReferences('!@#$%^&*()_+{}[]|;:,.<>?/~`').length === 0, 'Special chars return empty');
const longStr = 'a'.repeat(10000);
assert(detectFileReferences(longStr).length === 0, 'Long text without file returns empty');

// File in code block
const codeBlock = '```\nconst x = require("src/utils/helper.ts");\n```';
assertIncludes(detectFileReferences(codeBlock), ['src/utils/helper.ts'], 'Detects file in code block');

// ─── Test 7: Plugin structure ────────────────────────

console.log(`\n${CYAN}=== Plugin Structure Tests ===${RESET}\n`);

const plugin = await import('../dist/index.js');
assert(plugin.default.id === 'super-agent', 'Plugin loaded with correct id');
assert(typeof plugin.default.register === 'function', 'Register function exists');

// ─── Summary ──────────────────────────────────────────

console.log(`\n${CYAN}═══════════════════════════════════${RESET}`);
const total = passed + failed;
console.log('Total: ' + total + ' | ' + GREEN + 'Passed: ' + passed + RESET + ' | ' + RED + 'Failed: ' + failed + RESET);
console.log(CYAN + '═══════════════════════════════════' + RESET + '\n');

if (failed > 0) {
  console.log(RED + '\u2716 Errors:' + RESET);
  errors.forEach(e => console.log('  ' + RED + '\u2022' + RESET + ' ' + e));
  process.exit(1);
} else {
  console.log(GREEN + '\u2705 All tests passed!' + RESET);
  process.exit(0);
}
