/**
 * repo-mapper.ts — Core Repo Mapper module
 * 
 * Tree-sitter based TypeScript file analyzer.
 * Features:
 *   - mapFile(filePath) → imports + declarations
 *   - readCodeSymbol(filePath, symbolName) → zoom-in body
 *   - detectFileReferences(text) → file path matching
 * 
 * Upgraded: multi-language parsers + smart truncation
 */

import { existsSync } from 'node:fs';
import { relative } from 'node:path';
import { parseFile, isSupportedExt, LANGUAGE_NAMES, LanguageId } from './parsers/index.js';
import { extractSymbol, ExtractMode } from './extractor.js';

// ─── Lazy singleton parser ──────────────────────────────

const _parserPromise: Promise<void> = (async () => {
  // Warm up the base parser + default grammar (TS)
  // This ensures fast first-call latency
  try {
    await parseFile(process.cwd()); // triggers lazy init
  } catch {
    // expected — process.cwd() isn't a file; but parser initializes
  }
})();

async function ensureParser() {
  await _parserPromise;
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
  language: string;
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
  truncated: boolean;
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
  const { source, root, languageId } = await parseFile(filePath);
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
  return {
    file: filePathRel,
    size: source.length,
    lines: lines.length,
    language: LANGUAGE_NAMES[languageId] || languageId,
    imports,
    declarations,
  };
}

// ─── readCodeSymbol: Zoom-in tool ───────────────────────

export async function readCodeSymbol(
  filePath: string,
  symbolName: string,
  mode: ExtractMode = 'smart',
): Promise<SymbolBody | null> {
  const { source, root, languageId } = await parseFile(filePath);

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

    return extractSymbol(node, source, filePath, symbolName, node.type, mode, languageId);
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

      return extractSymbol(m, source, filePath, `${cName}.${symbolName}`, 'method', mode, languageId);
    }
  }

  return null;
}

// ─── detectFileReferences ───────────────────────────────

export function detectFileReferences(text: string): string[] {
  const results: string[] = [];

  // Extended path prefixes for auto-detection
  const PREFIXES = 'src|app|lib|components|hooks|utils|config|types|pages|web|data|dist|public|test|e2e|scripts|migrations|assets';
  const EXT = '(tsx|ts|jsx|json|js|mjs|css|sql|html|py|yaml|yml|toml|env)';
  const EXT_BARE = '(tsx|ts|jsx|json|js|mjs|css)';

  // Pattern 1: relative source paths (forward slash)
  const pat1 = new RegExp('(?:' + PREFIXES + ')[/\\\\][a-zA-Z0-9_\\\\\\-/.]+\\.' + EXT, 'g');
  results.push(...[...text.matchAll(pat1)].map(m => m[0].trim()));

  // Pattern 2: @/ alias paths
  const pat2 = /@\/[a-zA-Z0-9_\-/]+\.(tsx|ts|jsx|json|js|css|sql|py|html)/g;
  results.push(...[...text.matchAll(pat2)].map(m => m[0].trim()));

  // Pattern 3: bare filename with quotes (required to avoid false positives)
  const pat3 = new RegExp('["\\\'`]([a-zA-Z0-9_\\\\\\-]+\\.' + EXT_BARE + ')["\\\'`]', 'g');
  results.push(...[...text.matchAll(pat3)].map(m => m[1].trim()));

  return [...new Set(results.filter(Boolean))];
}
