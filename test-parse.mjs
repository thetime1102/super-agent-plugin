/**
 * test-parse.mjs — Tree-sitter AST Reduced Extraction
 * 
 * Parse 1 file .ts → xuất chỉ Class, Function Signatures, Interface, Type Alias
 * 
 * Chạy: node test-parse.mjs
 */

import { Parser, Language } from 'web-tree-sitter';
import { readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const TARGET_FILE = join(__dirname, '..', 'nhatvi-ecosystem-dev', 'src', 'services', 'llm.service.ts');

/**
 * Xử lý 1 declaration node (class, function, interface, type_alias, lexical_declaration)
 */
function extractOne(child, source) {
  const t = child.type;

  switch (t) {
    case 'class_declaration': {
      const nameNode = child.childForFieldName('name');
      const name = nameNode ? source.substring(nameNode.startIndex, nameNode.endIndex) : '<anon>';
      const bodyNode = child.childForFieldName('body');
      const methods = [];
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
      return {
        kind: 'class', name, methods,
        line: child.startPosition.row + 1, endLine: child.endPosition.row + 1,
      };
    }

    case 'function_declaration':
    case 'generator_function_declaration': {
      const fn = child.childForFieldName('name');
      const fp = child.childForFieldName('parameters');
      const fr = child.childForFieldName('return_type');
      const fName = fn ? source.substring(fn.startIndex, fn.endIndex) : '<anon>';
      const fParams = fp ? source.substring(fp.startIndex, fp.endIndex) : '()';
      const fReturn = fr ? source.substring(fr.startIndex, fr.endIndex) : '';
      const isAsync = source.substring(child.startIndex, child.endIndex).startsWith('async');
      return {
        kind: 'function', name: fName,
        signature: `${isAsync ? 'async ' : ''}${fName}${fParams}${fReturn}`,
        line: child.startPosition.row + 1, endLine: child.endPosition.row + 1,
      };
    }

    case 'interface_declaration': {
      const inName = child.childForFieldName('name');
      const name = inName ? source.substring(inName.startIndex, inName.endIndex) : '<anon>';
      const body = child.childForFieldName('body');
      const members = [];
      if (body) {
        for (let j = 0; j < body.childCount; j++) {
          const m = body.child(j);
          const txt = source.substring(m.startIndex, m.endIndex).replace(/\s+/g, ' ').trim();
          if (txt) members.push(txt);
        }
      }
      return {
        kind: 'interface', name, members,
        line: child.startPosition.row + 1, endLine: child.endPosition.row + 1,
      };
    }

    case 'type_alias_declaration': {
      const tn = child.childForFieldName('name');
      const name = tn ? source.substring(tn.startIndex, tn.endIndex) : '<anon>';
      return { kind: 'type_alias', name, line: child.startPosition.row + 1 };
    }

    case 'lexical_declaration': {
      const txt = source.substring(child.startIndex, child.endIndex).replace(/\s+/g, ' ').trim();
      return {
        kind: 'const',
        text: txt.length > 120 ? txt.substring(0, 120) + '...' : txt,
        line: child.startPosition.row + 1,
      };
    }

    default:
      return null;
  }
}

/**
 * Quét top-level declarations, unwrap export_statement
 */
function collectDeclarations(root, source) {
  const results = [];

  for (let i = 0; i < root.childCount; i++) {
    const child = root.child(i);
    let target = child;

    // UNWRAP export_statement → inner named declaration
    if (child.type === 'export_statement') {
      const inner = child.firstNamedChild;
      if (!inner) {
        // Re-export type { X }, export { X }, export * from ...
        const txt = source.substring(child.startIndex, child.endIndex).replace(/\s+/g, ' ').trim();
        if (/^export\s+(type\s+)?\{/.test(txt) || /^export\s+\*/.test(txt)) {
          results.push({ kind: 'export_other', line: child.startPosition.row + 1, text: txt.substring(0, 80) });
        }
        continue;
      }
      target = inner;
    }

    const r = extractOne(target, source);
    if (r) results.push(r);
  }

  return results;
}

async function main() {
  console.log('🔬  REPO MAPPER — Tree-sitter AST Extraction\n');

  // 1. Init WASM
  await Parser.init({
    locateFile(script) {
      return join(__dirname, 'node_modules', 'web-tree-sitter', script);
    },
  });

  // 2. Load TypeScript grammar
  const TS = await Language.load(
    join(__dirname, 'node_modules', 'tree-sitter-typescript', 'tree-sitter-typescript.wasm')
  );

  // 3. Parser
  const parser = new Parser();
  parser.setLanguage(TS);

  // 4. Read
  const source = readFileSync(TARGET_FILE, 'utf-8');
  const lines = source.split('\n');
  console.log(`📄  ${TARGET_FILE.split('nhatvi-ecosystem-dev\\')[1]}`);
  console.log(`    ${source.length} bytes, ${lines.length} lines\n`);

  // 5. Parse
  const tree = parser.parse(source);
  const root = tree.rootNode;
  console.log(`🌳  AST: ${root.descendantCount} nodes\n`);

  // 6. Extract
  const decls = collectDeclarations(root, source);

  const classes = decls.filter(d => d.kind === 'class');
  const ifaces  = decls.filter(d => d.kind === 'interface');
  const types   = decls.filter(d => d.kind === 'type_alias');
  const funcs   = decls.filter(d => d.kind === 'function');
  const consts  = decls.filter(d => d.kind === 'const');
  const exports = decls.filter(d => d.kind === 'export_other');

  // === PRINT ===
  console.log(`🔷  CLASSES (${classes.length}):`);
  for (const c of classes) {
    console.log(`     📘 ${c.name}  [L${c.line}-${c.endLine}]`);
    for (const m of c.methods) console.log(`        └─ ${m}`);
  }

  console.log(`\n🔶  INTERFACES (${ifaces.length}):`);
  for (const i of ifaces) {
    console.log(`     📐 ${i.name}  [L${i.line}-${i.endLine}]`);
    for (const m of i.members.slice(0, 8)) console.log(`        └─ ${m}`);
    if (i.members.length > 8) console.log(`        … +${i.members.length - 8} more`);
  }

  console.log(`\n🔷  TYPE ALIASES (${types.length}):`);
  for (const t of types) console.log(`     📎 ${t.name} (L${t.line})`);

  console.log(`\n🔶  FUNCTIONS (${funcs.length}):`);
  for (const f of funcs) {
    console.log(`     ⚡  ${f.signature}`);
    console.log(`         L${f.line}-${f.endLine}`);
  }

  console.log(`\n📦  TOP-LEVEL CONST/LET (${consts.length}):`);
  for (const c of consts) console.log(`     📦 L${c.line}: ${c.text}`);

  if (exports.length > 0) {
    console.log(`\n📤  EXPORT TYPE/RE-EXPORT (${exports.length}):`);
    for (const e of exports) console.log(`     📤 L${e.line}: ${e.text}`);
  }

  console.log(`\n${'─'.repeat(56)}`);
  console.log(`✅  Parsed ${root.descendantCount} AST nodes → ${decls.length} declarations`);
}

main().catch(err => {
  console.error('❌  Fatal:', err.message);
  process.exit(1);
});
