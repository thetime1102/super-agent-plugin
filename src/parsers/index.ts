/**
 * parsers/index.ts — Multi-language Parser Registry with lazy-load
 * 
 * Load grammar WASM on-demand, only when a file with matching extension
 * is actually opened. Saves memory (~4 MB WASM total).
 * 
 * Supported grammars:
 *   - TypeScript / TSX (tree-sitter-typescript) — default
 *   - JavaScript / JSX (reuses TS grammar)
 *   - Python (tree-sitter-python)
 *   - JSON (tree-sitter-json)
 *   - CSS (tree-sitter-css)
 */

import { readFileSync, existsSync } from 'node:fs';
import { join, dirname, extname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PLUGIN_DIST = resolve(__dirname, '..');

// ─── Types ─────────────────────────────────────────

export type LanguageId = 'ts' | 'tsx' | 'js' | 'jsx' | 'py' | 'json' | 'css';

export const LANGUAGE_NAMES: Record<LanguageId, string> = {
  ts:   'TypeScript',
  tsx:  'TSX',
  js:   'JavaScript',
  jsx:  'JSX',
  py:   'Python',
  json: 'JSON',
  css:  'CSS',
};

interface GrammarEntry {
  wasmFile: string;
  language: any | null;
  initPromise: Promise<void> | null;
}

const GRAMMAR_REGISTRY: Record<LanguageId, GrammarEntry> = {
  ts:   { wasmFile: 'tree-sitter-typescript.wasm', language: null, initPromise: null },
  tsx:  { wasmFile: 'tree-sitter-tsx.wasm',        language: null, initPromise: null },
  js:   { wasmFile: 'tree-sitter-typescript.wasm',  language: null, initPromise: null },
  jsx:  { wasmFile: 'tree-sitter-tsx.wasm',         language: null, initPromise: null },
  py:   { wasmFile: 'tree-sitter-python.wasm',      language: null, initPromise: null },
  json: { wasmFile: 'tree-sitter-json.wasm',        language: null, initPromise: null },
  css:  { wasmFile: 'tree-sitter-css.wasm',         language: null, initPromise: null },
};

const EXT_MAP: Record<string, LanguageId> = {
  '.ts': 'ts', '.tsx': 'tsx', '.js': 'js', '.jsx': 'jsx',
  '.mjs': 'js', '.cjs': 'js', '.mts': 'ts', '.cts': 'ts',
  '.py': 'py', '.json': 'json', '.css': 'css',
};

// ─── WASM resolution ───────────────────────────────

const WASM_PATHS = [
  PLUGIN_DIST,
  join(resolve(PLUGIN_DIST, '..'), 'dist'),
  join(resolve(PLUGIN_DIST, '..'), 'node_modules'),
];

function resolveWasm(wasmFile: string): string {
  // First try: wasm file name matches package name (tree-sitter-xxx.wasm in node_modules/tree-sitter-xxx/)
  for (const base of WASM_PATHS) {
    const pkgName = wasmFile.replace(/\.wasm$/, '');
    const pkgPath = join(base, pkgName, wasmFile);
    if (existsSync(pkgPath)) return pkgPath;
    // Direct path (dist/)
    const directPath = join(base, wasmFile);
    if (existsSync(directPath)) return directPath;
  }
  throw new Error(`Grammar WASM not found: ${wasmFile}`);
}

// ─── Base parser singleton ─────────────────────────

let _baseParser: any = null;
let _parserInitPromise: Promise<void> | null = null;

async function ensureBaseParser() {
  if (_baseParser) return;
  if (!_parserInitPromise) {
    _parserInitPromise = (async () => {
      const { Parser, Language } = await import('web-tree-sitter');
      await Parser.init({
        locateFile: (script: string) => {
          for (const base of WASM_PATHS) {
            // Try web-tree-sitter subdirectory first
            const p = join(base, 'web-tree-sitter', script);
            if (existsSync(p)) return p;
          }
          for (const base of WASM_PATHS) {
            const p = join(base, script);
            if (existsSync(p)) return p;
          }
          throw new Error(`WASM runtime not found: ${script}`);
        },
      });
      _baseParser = new Parser();
    })();
  }
  await _parserInitPromise;
}

// ─── Public API ────────────────────────────────────

/**
 * Get a parser for a specific file, lazy-loading grammar as needed
 */
export async function getParser(filePath: string): Promise<{ parser: any; languageId: LanguageId }> {
  const ext = extname(filePath).toLowerCase();
  const langId = EXT_MAP[ext];
  if (!langId) {
    throw new Error(`Unsupported file: ${ext} (supported: ${Object.keys(EXT_MAP).join(', ')})`);
  }

  const entry = GRAMMAR_REGISTRY[langId];
  await ensureBaseParser();

  if (!entry.language) {
    if (!entry.initPromise) {
      entry.initPromise = (async () => {
        const { Language } = await import('web-tree-sitter');
        const wasmPath = resolveWasm(entry.wasmFile);
        entry.language = await Language.load(wasmPath);
      })();
    }
    await entry.initPromise;
  }

  _baseParser.setLanguage(entry.language);
  return { parser: _baseParser, languageId: langId };
}

/**
 * Parse file → AST root + source
 */
export async function parseFile(filePath: string): Promise<{
  source: string; root: any; languageId: LanguageId;
}> {
  if (!existsSync(filePath)) {
    throw new Error(`File not found: ${filePath}`);
  }
  const source = readFileSync(filePath, 'utf-8');
  const { parser, languageId } = await getParser(filePath);
  const tree = parser.parse(source);
  return { source, root: tree.rootNode, languageId };
}

/**
 * Check if extension is supported
 */
export function isSupportedExt(filePath: string): boolean {
  return extname(filePath).toLowerCase() in EXT_MAP;
}
