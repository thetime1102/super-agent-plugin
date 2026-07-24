import { Parser, Language } from 'web-tree-sitter';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { readFileSync } from 'fs';

const __dirname = dirname(fileURLToPath(import.meta.url));

async function main() {
  await Parser.init({ locateFile: (s) => join(__dirname, 'node_modules', 'web-tree-sitter', s) });
  const TS = await Language.load(join(__dirname, 'node_modules', 'tree-sitter-typescript', 'tree-sitter-typescript.wasm'));
  const p = new Parser();
  p.setLanguage(TS);

  const srcFile = join(__dirname, '..', 'nhatvi-ecosystem-dev', 'src', 'services', 'llm.service.ts');
  const source = readFileSync(srcFile, 'utf-8');
  const tree = p.parse(source);
  const root = tree.rootNode;

  // Print children that ARE export_statement
  for (let i = 0; i < root.childCount; i++) {
    const child = root.child(i);
    if (child.type !== 'export_statement') continue;
    
    console.log(`\n[${i}] export_statement (L${child.startPosition.row + 1}):`);
    console.log(`  Children: ${child.childCount}`);
    console.log(`  NamedChildren: ${child.namedChildCount}`);
    console.log(`  firstChild: type="${child.firstChild?.type}"`);
    console.log(`  firstNamedChild: type="${child.firstNamedChild?.type}"`);
    
    // Print all children
    for (let j = 0; j < child.childCount; j++) {
      const c = child.child(j);
      console.log(`    [${j}] type="${c?.type}" isNamed=${c?.isNamed} text="${source.substring(c.startIndex, c.endIndex).replace(/\s+/g, ' ').trim().substring(0, 60)}"`);
    }
  }
}

main().catch(e => console.error(e.message));
