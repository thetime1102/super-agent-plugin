import { Parser, Language } from 'web-tree-sitter';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const __dirname = dirname(fileURLToPath(import.meta.url));

async function test() {
  await Parser.init({
    locateFile: (s) => join(__dirname, 'node_modules', 'web-tree-sitter', s),
  });

  // Try TypeScript grammar
  const tsWasm = join(__dirname, 'node_modules', 'tree-sitter-typescript', 'tree-sitter-typescript.wasm');
  const TS = await Language.load(tsWasm);
  const p = new Parser();
  p.setLanguage(TS);

  const code = `export class Hello { name: string; greet() { return "hi"; } }`;
  const tree = p.parse(code);
  console.log('Root:', tree.rootNode.type);
  console.log('Children count:', tree.rootNode.childCount);
  for (let i = 0; i < tree.rootNode.childCount; i++) {
    const c = tree.rootNode.child(i);
    console.log(`  Child ${i}: type="${c.type}" text="${code.substring(c.startIndex, c.endIndex)}"`);
  }

  // Try the llm.service.ts first few lines
  console.log('\n--- Now parsing llm.service.ts ---');
  const { readFileSync } = await import('fs');
  const srcFile = join(__dirname, '..', 'nhatvi-ecosystem-dev', 'src', 'services', 'llm.service.ts');
  const source = readFileSync(srcFile, 'utf-8');
  const tree2 = p.parse(source);
  const root = tree2.rootNode;
  console.log('Root:', root.type, '- children:', root.childCount);
  for (let i = 0; i < Math.min(15, root.childCount); i++) {
    const c = root.child(i);
    const txt = source.substring(c.startIndex, c.endIndex).replace(/\s+/g, ' ').trim();
    console.log(`  [${i}] type="${c.type}" text="${txt.substring(0, 100)}"`);
  }
}

test().catch(e => console.error('ERR:', e.message));
