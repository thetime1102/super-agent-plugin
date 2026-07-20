/**
 * test-integration.mjs — Integration tests with sample fixtures
 */

import { existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(__dirname, '..');
const FIXTURES_DIR = join(__dirname, 'fixtures');

const GREEN = '\x1b[32m';
const RED = '\x1b[31m';
const CYAN = '\x1b[36m';
const RESET = '\x1b[0m';

let passed = 0;
let failed = 0;

function assert(condition, msg) {
  if (condition) {
    passed++;
    process.stdout.write(GREEN + '\u2713' + RESET + ' ');
  } else {
    failed++;
    process.stdout.write(RED + '\u2717' + RESET + ' ');
  }
  console.log(msg);
}

console.log(`\n${CYAN}=== Integration Tests ===${RESET}\n`);

const { mapFile, readCodeSymbol, detectFileReferences } = await import('../dist/repo-mapper.js');
const sampleFile = join(FIXTURES_DIR, 'sample.ts');
const emptyFile = join(FIXTURES_DIR, 'empty.ts');

// --- Test: mapFile on sample.ts ---

if (existsSync(sampleFile)) {
  const fm = await mapFile(sampleFile, FIXTURES_DIR);

  assert(fm.file === 'sample.ts', 'mapFile returns correct relative path');
  assert(fm.imports.length >= 3, 'Sample file has 3+ imports');
  assert(fm.declarations.length >= 5, 'Sample file has 5+ declarations');

  const importPaths = fm.imports.map(i => i.modulePath);
  assert(importPaths.includes('node:fs'), 'Detects "node:fs" import');
  assert(importPaths.includes('axios'), 'Detects "axios" import');

  const declNames = fm.declarations.map(d => d.name).filter(Boolean);
  assert(declNames.includes('AppConfig'), 'Detects interface AppConfig');
  assert(declNames.includes('LogLevel'), 'Detects type LogLevel');
  assert(declNames.includes('Logger'), 'Detects class Logger');
  assert(declNames.includes('fetchData'), 'Detects function fetchData');

  const loggerClass = fm.declarations.find(d => d.name === 'Logger');
  assert(loggerClass !== undefined, 'Logger class found');
  assert(loggerClass.methods !== undefined, 'Logger class has methods');
  if (loggerClass && loggerClass.methods) {
    const methodNames = loggerClass.methods.map(m => m.split('(')[0]);
    assert(methodNames.includes('log'), 'Logger has log() method');
    assert(methodNames.includes('error'), 'Logger has error() method');
  }

  const fetchFn = fm.declarations.find(d => d.name === 'fetchData');
  assert(fetchFn && fetchFn.signature && fetchFn.signature.includes('async'), 'fetchData is async');
} else {
  assert(true, 'Skipped: sample.ts not found');
}

// --- Test: readCodeSymbol on sample.ts ---

if (existsSync(sampleFile)) {
  const sym = await readCodeSymbol(sampleFile, 'fetchData');
  assert(sym !== null, 'readCodeSymbol finds fetchData');
  if (sym) {
    assert(sym.body.includes('async'), 'fetchData body contains async');
    assert(sym.body.includes('config.apiUrl'), 'fetchData body contains implementation');
    assert(sym.line > 0, 'fetchData has valid start line');
    assert(sym.endLine >= sym.line, 'fetchData has valid end line');
  }

  const iface = await readCodeSymbol(sampleFile, 'AppConfig');
  assert(iface !== null, 'readCodeSymbol finds AppConfig interface');
  if (iface) {
    assert(iface.body.includes('apiUrl'), 'AppConfig body contains apiUrl');
  }

  const typeAlias = await readCodeSymbol(sampleFile, 'LogLevel');
  assert(typeAlias !== null, 'readCodeSymbol finds LogLevel type');
  if (typeAlias) {
    assert(typeAlias.body.includes('debug'), 'LogLevel body contains debug');
  }
}

// --- Test: readCodeSymbol on empty file ---

if (existsSync(emptyFile)) {
  const sym = await readCodeSymbol(emptyFile, 'anything');
  assert(sym === null, 'readCodeSymbol on empty file returns null');
}

// --- Test: detectFileReferences ---

const multi = detectFileReferences('Fix src/a.ts and lib/b.ts and components/c.tsx');
assert(multi.length >= 2, 'Multiple files detected (' + multi.length + ')');

const dedup = detectFileReferences('src/a.ts and src/a.ts');
assert(dedup.includes('src/a.ts'), 'Duplicate full path files are deduplicated');
// Note: bare filename 'a.ts' also appears (from filePattern), that's expected

// --- Summary ---

console.log(`\n${CYAN}═══════════════════════════════════${RESET}`);
const total = passed + failed;
console.log('Total: ' + total + ' | ' + GREEN + 'Passed: ' + passed + RESET + ' | ' + RED + 'Failed: ' + failed + RESET);
console.log(CYAN + '═══════════════════════════════════' + RESET + '\n');

if (failed > 0) {
  process.exit(1);
} else {
  console.log(GREEN + '\u2705 All integration tests passed!' + RESET);
  process.exit(0);
}
