/**
 * repo-mapper.ts — Core Repo Mapper module
 * 
 * Tree-sitter based TypeScript file analyzer.
 * Features:
 *   - mapFile(filePath) → imports + declarations
 *   - readCodeSymbol(filePath, symbolName) → zoom-in body
 *   - detectFileReferences(text) → file path matching
 * 
 * ⚠️  WASM files được copy vào dist/ khi build.
 *    Đường dẫn resolve động qua __dirname để chạy được ở mọi máy.
 */

import { readFileSync, existsSync } from 'node:fs';
import { relative, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const WASM_DIR = __dirname; // .wasm files are copied to dist/ at build time

// ─── Lazy singleton parser ──────────────────────────────

let _initPromise: Promise<void> | null = null;
let _parser: any = null;

async function ensureParser() {
  if (_parser) return;
  if (!_initPromise) {
    _initPromise = (async () => {
      const { Parser, Language } = await import('web-tree-sitter');
      
      // Init with locateFile để tìm .wasm (ưu tiên dist/, fallback node_modules)
      await Parser.init({
        locateFile: (script: string) => {
          // Ưu tiên file trong dist/ (đã copy)
          const distPath = join(WASM_DIR, script);
          if (existsSync(distPath)) return distPath;
          // Fallback: node_modules (development)
          return join(WASM_DIR, '..', 'node_modules', 'web-tree-sitter', script);
        },
      });

      // Load TypeScript grammar WASM từ dist/ (đã copy lúc build)
      const grammarWasm = join(WASM_DIR, 'tree-sitter-typescript.wasm');
      const fallbackWasm = join(WASM_DIR, '..', 'node_modules', 'tree-sitter-typescript', 'tree-sitter-typescript.wasm');
      
      const TS = await Language.load(existsSync(grammarWasm) ? grammarWasm : fallbackWasm);

      _parser = new Parser();
      _parser.setLanguage(TS);
    })();
  }
  await _initPromise;
}

async function parseFile(filePath: string) {
  if (!existsSync(filePath)) {
    throw new Error(`File not found: ${filePath}`);
  }
  await ensureParser();
  const source = readFileSync(filePath, 'utf-8');
  const tree = _parser.parse(source);
  return { source, root: tree.rootNode };
}

// ─── Types ──────────────────────────────────────────────

export interface ImportEntry {
  modulePath: string;
  specifiers?: string[];
  line: number;
}

export interface Declaration {
  kind: 'class' | 'function' | 'interface' | 'type_alias' | 'const' | 'export_other';
  name?: string;
  signature?: string;
  methods?: string[];
  members?: string[];
  text?: string;
  line: number;
  endLine?: number;
}

export interface FileMap {
  file: string;
  size: number;
  lines: number;
  imports: ImportEntry[];
  declarations: Declaration[];
}

export interface SymbolBody {
  symbolName: string;
  kind: string;
  file: string;
  line: number;
  endLine: number;
  body: string;
}

// ─── Extract single declaration ─────────────────────────

function extractOne(node: any, source: string): Declaration | null {
  const t = node.type;

  switch (t) {
    case 'class_declaration': {
      const nameNode = node.childForFieldName('name');
      const name = nameNode ? source.substring(nameNode.startIndex, nameNode.endIndex) : '<anon>';
      const bodyNode = node.childForFieldName('body');
      const methods: string[] = [];
      if (bodyNode) {
        for (let j = 0; j < bodyNode.childCount; j++) {
          const m = bodyNode.child(j);
          if (m.type === 'method_definition') {
            const mn = m.childForFieldName('name');
            const mp = m.childForFieldName('parameters');
            const mr = m.childForFieldName('return_type');
            const mName = mn ? source.substring(mn.startIndex, mn.endIndex) : '?';
            const mParams = mp ? source.substring(mp.startIndex, mp.endIndex) : '()';
            const mReturn = mr ? source.substring(mr.startIndex, mr.endIndex) : '';
            methods.push(`${mName}${mParams}${mReturn}`);
          }
        }
      }
      return { kind: 'class', name, methods,
        line: node.startPosition.row + 1, endLine: node.endPosition.row + 1 };
    }

    case 'function_declaration':
    case 'generator_function_declaration': {
      const fn = node.childForFieldName('name');
      const fp = node.childForFieldName('parameters');
      const fr = node.childForFieldName('return_type');
      const fName = fn ? source.substring(fn.startIndex, fn.endIndex) : '<anon>';
      const fParams = fp ? source.substring(fp.startIndex, fp.endIndex) : '()';
      const fReturn = fr ? source.substring(fr.startIndex, fr.endIndex) : '';
      const isAsync = source.substring(node.startIndex, node.endIndex).startsWith('async');
      return {
        kind: 'function', name: fName,
        signature: `${isAsync ? 'async ' : ''}${fName}${fParams}${fReturn}`,
        line: node.startPosition.row + 1, endLine: node.endPosition.row + 1,
      };
    }

    case 'interface_declaration': {
      const inName = node.childForFieldName('name');
      const name = inName ? source.substring(inName.startIndex, inName.endIndex) : '<anon>';
      const body = node.childForFieldName('body');
      const members: string[] = [];
      if (body) {
        for (let j = 0; j < body.childCount; j++) {
          const m = body.child(j);
          const txt = source.substring(m.startIndex, m.endIndex).replace(/\s+/g, ' ').trim();
          if (txt && txt !== '{' && txt !== '}' && txt !== ';') members.push(txt);
        }
      }
      return { kind: 'interface', name, members,
        line: node.startPosition.row + 1, endLine: node.endPosition.row + 1 };
    }

    case 'type_alias_declaration': {
      const tn = node.childForFieldName('name');
      const name = tn ? source.substring(tn.startIndex, tn.endIndex) : '<anon>';
      return { kind: 'type_alias', name, line: node.startPosition.row + 1 };
    }

    case 'lexical_declaration': {
      const txt = source.substring(node.startIndex, node.endIndex).replace(/\s+/g, ' ').trim();
      return {
        kind: 'const',
        text: txt.length > 120 ? txt.substring(0, 120) + '...' : txt,
        line: node.startPosition.row + 1,
      };
    }

    default:
      return null;
  }
}

// ─── Extract imports ────────────────────────────────────

function extractImports(root: any, source: string): ImportEntry[] {
  const imports: ImportEntry[] = [];

  for (let i = 0; i < root.childCount; i++) {
    const child = root.child(i);
    if (child.type !== 'import_statement') continue;

    const sourceNode = child.firstNamedChild;
    if (!sourceNode) continue;

    let modulePath = '';
    let specifiers: string[] = [];

    if (sourceNode.type === 'string') {
      modulePath = source.substring(sourceNode.startIndex, sourceNode.endIndex).replace(/['"]/g, '');
      for (let j = 0; j < child.childCount; j++) {
        const s = child.child(j);
        if (s.type === 'import_clause' || s.type === 'namespace_import') {
          specifiers.push(source.substring(s.startIndex, s.endIndex).replace(/\s+/g, ' ').trim());
        }
      }
    } else if (sourceNode.type === 'import_clause' || sourceNode.type === 'namespace_import') {
      specifiers.push(source.substring(sourceNode.startIndex, sourceNode.endIndex).replace(/\s+/g, ' ').trim());
      for (let j = 0; j < child.childCount; j++) {
        if (child.child(j).type === 'string') {
          modulePath = source.substring(child.child(j).startIndex, child.child(j).endIndex).replace(/['"]/g, '');
        }
      }
    }

    if (modulePath) {
      imports.push({
        modulePath,
        specifiers: specifiers.length > 0 ? specifiers : undefined,
        line: child.startPosition.row + 1,
      });
    }
  }

  return imports;
}

// ─── mapFile: Full file analysis ────────────────────────

export async function mapFile(filePath: string, rootDir?: string): Promise<FileMap> {
  const { source, root } = await parseFile(filePath);
  const lines = source.split('\n');

  const imports: ImportEntry[] = extractImports(root, source);
  const declarations: Declaration[] = [];

  for (let i = 0; i < root.childCount; i++) {
    let node = root.child(i);
    if (node.type === 'import_statement') continue;

    // Unwrap export
    if (node.type === 'export_statement') {
      const inner = node.firstNamedChild;
      if (!inner) {
        const txt = source.substring(node.startIndex, node.endIndex).replace(/\s+/g, ' ').trim();
        if (/^export\s+(type\s+)?\{/.test(txt) || /^export\s+\*/.test(txt)) {
          declarations.push({ kind: 'export_other', line: node.startPosition.row + 1, text: txt.substring(0, 80) });
        }
        continue;
      }
      node = inner;
    }

    const r = extractOne(node, source);
    if (r) declarations.push(r);
  }

  const filePathRel = rootDir ? relative(rootDir, filePath) : filePath;
  return { file: filePathRel, size: source.length, lines: lines.length, imports, declarations };
}

// ─── readCodeSymbol: Zoom-in tool ───────────────────────

export async function readCodeSymbol(filePath: string, symbolName: string): Promise<SymbolBody | null> {
  const { source, root } = await parseFile(filePath);

  // Search top-level declarations
  for (let i = 0; i < root.childCount; i++) {
    let node = root.child(i);

    if (node.type === 'export_statement') {
      const inner = node.firstNamedChild;
      if (inner) node = inner;
      else continue;
    }

    const nameNode = node.childForFieldName('name');
    if (!nameNode) continue;
    const name = source.substring(nameNode.startIndex, nameNode.endIndex);
    if (name !== symbolName) continue;

    return {
      symbolName,
      kind: node.type,
      file: filePath,
      line: node.startPosition.row + 1,
      endLine: node.endPosition.row + 1,
      body: source.substring(node.startIndex, node.endIndex),
    };
  }

  // Search class methods
  for (let i = 0; i < root.childCount; i++) {
    let node = root.child(i);
    if (node.type === 'export_statement') {
      const inner = node.firstNamedChild;
      if (inner) node = inner;
      else continue;
    }
    if (node.type !== 'class_declaration') continue;

    const body = node.childForFieldName('body');
    if (!body) continue;
    const classNameNode = node.childForFieldName('name');
    const cName = classNameNode ? source.substring(classNameNode.startIndex, classNameNode.endIndex) : '?';

    for (let j = 0; j < body.childCount; j++) {
      const m = body.child(j);
      if (m.type !== 'method_definition') continue;
      const mn = m.childForFieldName('name');
      if (!mn) continue;
      if (source.substring(mn.startIndex, mn.endIndex) !== symbolName) continue;

      return {
        symbolName: `${cName}.${symbolName}`,
        kind: 'method',
        file: filePath,
        line: m.startPosition.row + 1,
        endLine: m.endPosition.row + 1,
        body: source.substring(m.startIndex, m.endIndex),
      };
    }
  }

  return null;
}

// ─── detectFileReferences ───────────────────────────────

/**
 * Quét user message tìm patterns giống file path
 * VD: "src/services/llm.service.ts" hoặc "llm.service.ts"
 */
export function detectFileReferences(text: string): string[] {
  // Pattern 1: src/path/file.ts (relative source paths)
  const pathPattern = /(?:src|web|data|dist|public)\/[a-zA-Z0-9_\-/.]+\.(ts|tsx|js|jsx|css|json|sql)/g;
  const pathMatches = [...text.matchAll(pathPattern)].map(m => m[0].trim());

  // Pattern 2: "file.ts" named with extension (quoted or bare)
  const filePattern = /["'`]?([a-zA-Z0-9_\-]+\.(ts|tsx|js|jsx|css|json))["'`]?/g;
  const fileMatches = [...text.matchAll(filePattern)].map(m => m[1].trim());

  // Deduplicate
  return [...new Set([...pathMatches, ...fileMatches])];
}
