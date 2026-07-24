/**
 * repo-mapper.mjs — Tree-sitter Repo Mapper Module
 * 
 * Features:
 *   1. Parse AST → Class, Function, Interface, Type Alias
 *   2. Extract Imports → Dependency Graph
 *   3. read_symbol_body(filePath, symbolName) → Zoom-in tool
 * 
 * Chạy: node repo-mapper.mjs
 */

import { Parser, Language } from 'web-tree-sitter';
import { readFileSync, existsSync } from 'fs';
import { join, dirname, relative, resolve } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = join(__dirname, '..', 'nhatvi-ecosystem-dev');

// ─── GRAMMAR CACHE ───────────────────────────────────────

let _parser = null;

async function getParser() {
  if (_parser) return _parser;
  
  await Parser.init({
    locateFile: (script) => join(__dirname, 'node_modules', 'web-tree-sitter', script),
  });
  
  const TS = await Language.load(
    join(__dirname, 'node_modules', 'tree-sitter-typescript', 'tree-sitter-typescript.wasm')
  );
  
  _parser = new Parser();
  _parser.setLanguage(TS);
  return _parser;
}

// ─── PARSE FILE TO AST ───────────────────────────────────

async function parseFile(filePath) {
  if (!existsSync(filePath)) {
    throw new Error(`File not found: ${filePath}`);
  }
  const source = readFileSync(filePath, 'utf-8');
  const parser = await getParser();
  const tree = parser.parse(source);
  return { source, tree, root: tree.rootNode };
}

// ─── 1. EXTRACT DECLARATIONS ─────────────────────────────

function extractDeclaration(node, source) {
  const t = node.type;
  
  switch (t) {
    case 'class_declaration': {
      const nameNode = node.childForFieldName('name');
      const name = nameNode ? source.substring(nameNode.startIndex, nameNode.endIndex) : '<anon>';
      const bodyNode = node.childForFieldName('body');
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
      return { kind: 'class', name, methods,
        line: node.startPosition.row + 1, endLine: node.endPosition.row + 1,
        startIndex: node.startIndex, endIndex: node.endIndex };
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
      return { kind: 'function', name: fName,
        signature: `${isAsync ? 'async ' : ''}${fName}${fParams}${fReturn}`,
        line: node.startPosition.row + 1, endLine: node.endPosition.row + 1,
        startIndex: node.startIndex, endIndex: node.endIndex };
    }

    case 'interface_declaration': {
      const inName = node.childForFieldName('name');
      const name = inName ? source.substring(inName.startIndex, inName.endIndex) : '<anon>';
      const body = node.childForFieldName('body');
      const members = [];
      if (body) {
        for (let j = 0; j < body.childCount; j++) {
          const m = body.child(j);
          const txt = source.substring(m.startIndex, m.endIndex).replace(/\s+/g, ' ').trim();
          // Skip punctuation-only members
          if (txt && txt !== '{' && txt !== '}' && txt !== ';') members.push(txt);
        }
      }
      return { kind: 'interface', name, members,
        line: node.startPosition.row + 1, endLine: node.endPosition.row + 1,
        startIndex: node.startIndex, endIndex: node.endIndex };
    }

    case 'type_alias_declaration': {
      const tn = node.childForFieldName('name');
      const name = tn ? source.substring(tn.startIndex, tn.endIndex) : '<anon>';
      return { kind: 'type_alias', name,
        line: node.startPosition.row + 1,
        startIndex: node.startIndex, endIndex: node.endIndex };
    }

    case 'lexical_declaration': {
      const txt = source.substring(node.startIndex, node.endIndex).replace(/\s+/g, ' ').trim();
      return { kind: 'const',
        text: txt.length > 120 ? txt.substring(0, 120) + '...' : txt,
        line: node.startPosition.row + 1,
        startIndex: node.startIndex, endIndex: node.endIndex };
    }

    default:
      return null;
  }
}

// ─── 2. EXTRACT IMPORTS (DEPENDENCY GRAPH) ───────────────

function extractImports(root, source) {
  const imports = [];
  
  for (let i = 0; i < root.childCount; i++) {
    const child = root.child(i);
    if (child.type !== 'import_statement') continue;
    
    // Find the source string
    const sourceNode = child.firstNamedChild;
    if (!sourceNode) continue;
    
    let modulePath = '';
    let specifiers = [];
    
    // import { X, Y } from 'z' OR import X from 'z' OR import 'z'
    if (sourceNode.type === 'string') {
      // import 'something' or import X from 'something'
      modulePath = source.substring(sourceNode.startIndex, sourceNode.endIndex).replace(/['"]/g, '');
      
      // Find specifiers (named imports or default import)
      for (let j = 0; j < child.childCount; j++) {
        const s = child.child(j);
        if (s.type === 'import_clause' || s.type === 'namespace_import') {
          const txt = source.substring(s.startIndex, s.endIndex).replace(/\s+/g, ' ').trim();
          specifiers.push(txt);
        }
      }
    } else if (sourceNode.type === 'import_clause' || sourceNode.type === 'namespace_import') {
      // import X from 'z' style — sourceNode is the clause, need to find string
      const def = source.substring(sourceNode.startIndex, sourceNode.endIndex).replace(/\s+/g, ' ').trim();
      specifiers.push(def);
      
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

// ─── 3. READ SYMBOL BODY (ZOOM-IN) ───────────────────────

async function read_symbol_body(filePath, symbolName) {
  const { source, root } = await parseFile(filePath);
  
  // Walk top-level + unwrap exports
  for (let i = 0; i < root.childCount; i++) {
    let node = root.child(i);
    
    // Unwrap export_statement
    if (node.type === 'export_statement') {
      const inner = node.firstNamedChild;
      if (inner) node = inner;
      else continue;
    }
    
    // Check if this node's name matches
    const nameNode = node.childForFieldName('name');
    if (!nameNode) continue;
    
    const name = source.substring(nameNode.startIndex, nameNode.endIndex);
    if (name !== symbolName) continue;
    
    // Found! Return the body
    return {
      symbolName,
      kind: node.type,
      file: filePath,
      line: node.startPosition.row + 1,
      endLine: node.endPosition.row + 1,
      body: source.substring(node.startIndex, node.endIndex),
      startIndex: node.startIndex,
      endIndex: node.endIndex,
    };
  }
  
  // Not found at top-level — search deeper (method inside class?)
  for (let i = 0; i < root.childCount; i++) {
    let node = root.child(i);
    if (node.type === 'export_statement') {
      const inner = node.firstNamedChild;
      if (inner) node = inner;
      else continue;
    }
    
    // Class body?
    if (node.type === 'class_declaration') {
      const body = node.childForFieldName('body');
      if (!body) continue;
      
      for (let j = 0; j < body.childCount; j++) {
        const m = body.child(j);
        if (m.type !== 'method_definition') continue;
        
        const mn = m.childForFieldName('name');
        if (!mn) continue;
        
        const mName = source.substring(mn.startIndex, mn.endIndex);
        if (mName !== symbolName) continue;
        
        const className = source.substring(node.childForFieldName('name').startIndex, node.childForFieldName('name').endIndex);
        return {
          symbolName: `${className}.${symbolName}`,
          kind: 'method',
          file: filePath,
          line: m.startPosition.row + 1,
          endLine: m.endPosition.row + 1,
          body: source.substring(m.startIndex, m.endIndex),
          startIndex: m.startIndex,
          endIndex: m.endIndex,
        };
      }
    }
  }
  
  return null; // Not found
}

// ─── 4. FULL FILE MAP ────────────────────────────────────

async function mapFile(filePath) {
  const { source, root } = await parseFile(filePath);
  const lines = source.split('\n');
  
  // Declarations
  const decls = [];
  const imports = extractImports(root, source);
  
  for (let i = 0; i < root.childCount; i++) {
    let node = root.child(i);
    
    if (node.type === 'import_statement') continue;
    
    // Unwrap export
    if (node.type === 'export_statement') {
      const inner = node.firstNamedChild;
      if (!inner) {
        const txt = source.substring(node.startIndex, node.endIndex).replace(/\s+/g, ' ').trim();
        if (/^export\s+(type\s+)?\{/.test(txt) || /^export\s+\*/.test(txt)) {
          decls.push({ kind: 'export_other', line: node.startPosition.row + 1, text: txt.substring(0, 80) });
        }
        continue;
      }
      node = inner;
    }
    
    const r = extractDeclaration(node, source);
    if (r) decls.push(r);
  }
  
  return {
    file: relative(PROJECT_ROOT, filePath),
    size: source.length,
    lines: lines.length,
    imports,
    declarations: decls,
  };
}

// ─── MAIN ────────────────────────────────────────────────

async function main() {
  console.log('🔬  REPO MAPPER — Full Module Test\n');
  
  const targetFile = join(PROJECT_ROOT, 'src', 'services', 'llm.service.ts');
  
  // ─── Test 1: Full File Map ───
  console.log('='.repeat(60));
  console.log('📋  TEST 1: FULL FILE MAP');
  console.log('='.repeat(60));
  
  const fileMap = await mapFile(targetFile);
  
  console.log(`\n📄  ${fileMap.file}`);
  console.log(`    ${fileMap.size} bytes, ${fileMap.lines} lines\n`);
  
  // Imports
  console.log(`🔗  IMPORTS (${fileMap.imports.length} dependencies):`);
  const grouped = {};
  for (const imp of fileMap.imports) {
    const isLocal = imp.modulePath.startsWith('.');
    const category = isLocal ? '📁 Local' : '📦 External';
    if (!grouped[category]) grouped[category] = [];
    grouped[category].push(`  ${imp.modulePath}${imp.specifiers ? ' → ' + imp.specifiers.join(', ') : ''}`);
  }
  for (const [cat, items] of Object.entries(grouped)) {
    console.log(`   ${cat}:`);
    for (const item of items) console.log(`      ${item}`);
  }
  
  // Declarations
  const classes = fileMap.declarations.filter(d => d.kind === 'class');
  const ifaces  = fileMap.declarations.filter(d => d.kind === 'interface');
  const types   = fileMap.declarations.filter(d => d.kind === 'type_alias');
  const funcs   = fileMap.declarations.filter(d => d.kind === 'function');
  const consts  = fileMap.declarations.filter(d => d.kind === 'const');
  
  console.log(`\n🔷  DECLARATIONS (${fileMap.declarations.length}):`);
  console.log(`     ${classes.length} classes`);
  console.log(`     ${ifaces.length} interfaces`);
  console.log(`     ${types.length} type aliases`);
  console.log(`     ${funcs.length} functions`);
  console.log(`     ${consts.length} consts`);
  
  // ─── Test 2: read_symbol_body ───
  console.log('\n' + '='.repeat(60));
  console.log('📋  TEST 2: READ_SYMBOL_BODY (Zoom-in)');
  console.log('='.repeat(60));
  
  const symbolResult = await read_symbol_body(targetFile, 'callDeepSeek');
  if (symbolResult) {
    console.log(`\n🔍  Zoom-in: ${symbolResult.symbolName}`);
    console.log(`    File: ${relative(PROJECT_ROOT, symbolResult.file)}`);
    console.log(`    Lines: ${symbolResult.line} - ${symbolResult.endLine}`);
    console.log(`    Body length: ${symbolResult.body.length} chars\n`);
    console.log('─'.repeat(60));
    console.log(symbolResult.body);
    console.log('─'.repeat(60));
  } else {
    console.log('❌  Symbol not found!');
  }
  
  // ─── Test 3: read_symbol_body on interface ───
  console.log('\n\n' + '='.repeat(60));
  console.log('📋  TEST 3: READ_SYMBOL_BODY — Interface');
  console.log('='.repeat(60));
  
  const ifaceResult = await read_symbol_body(targetFile, 'AlbumPhotoItem');
  if (ifaceResult) {
    console.log(`\n🔍  Zoom-in: ${ifaceResult.symbolName}`);
    console.log(`    Lines: ${ifaceResult.line} - ${ifaceResult.endLine}\n`);
    console.log('─'.repeat(60));
    console.log(ifaceResult.body);
    console.log('─'.repeat(60));
  } else {
    console.log('❌  Interface not found!');
  }
  
  // ─── Test 4: Non-existent symbol ───
  console.log('\n\n' + '='.repeat(60));
  console.log('📋  TEST 4: SYMBOL NOT FOUND');
  console.log('='.repeat(60));
  
  const missing = await read_symbol_body(targetFile, 'nonExistentFunction');
  console.log(`\n🔍  Looking for "nonExistentFunction": ${missing === null ? '✅ null (correct)' : '❌ found unexpectedly'}`);
}

main().catch(err => {
  console.error('❌  Fatal:', err.message);
  process.exit(1);
});
