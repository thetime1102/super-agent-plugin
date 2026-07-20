/**
 * copy-wasm.mjs — Copy .wasm files từ node_modules vào dist/
 * Chạy sau tsc build, từ thư mục gốc project
 */
import { copyFileSync, mkdirSync, existsSync } from 'node:fs';
import { join, dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

// Resolve project root = thư mục chứa node_modules (parent của scripts/)
const __dirname = dirname(fileURLToPath(import.meta.url));
const PROJECT_ROOT = resolve(__dirname, '..');
const distDir = join(PROJECT_ROOT, 'dist');
const nmDir = join(PROJECT_ROOT, 'node_modules');

console.log(`📁 Project root: ${PROJECT_ROOT}`);
console.log(`📁 Dist dir: ${distDir}`);
console.log(`📁 Node modules: ${nmDir}\n`);

if (!existsSync(distDir)) {
  mkdirSync(distDir, { recursive: true });
}

const wasmFiles = [
  ['tree-sitter-typescript', 'tree-sitter-typescript.wasm'],
  ['tree-sitter-typescript', 'tree-sitter-tsx.wasm'],
  ['web-tree-sitter', 'web-tree-sitter.wasm'],
];

let copied = 0;
let failed = 0;

for (const [pkg, file] of wasmFiles) {
  const src = join(nmDir, pkg, file);
  const dest = join(distDir, file);
  if (existsSync(src)) {
    copyFileSync(src, dest);
    console.log(`✅  ${pkg}/${file} → ${dest.replace(PROJECT_ROOT, '.')}`);
    copied++;
  } else {
    console.warn(`⚠️  NOT FOUND: ${src.replace(PROJECT_ROOT, '.')}`);
    failed++;
  }
}

// Copy package.json + openclaw.plugin.json cho npm publish
for (const file of ['package.json', 'openclaw.plugin.json', 'README.md', 'LICENSE']) {
  const src = join(PROJECT_ROOT, file);
  const dest = join(distDir, file);
  if (existsSync(src)) {
    copyFileSync(src, dest);
    console.log(`✅  ${file} → dist/${file}`);
  }
}

console.log(`\n📦 WASM: ${copied} copied, ${failed} failed. Ready in dist/`);
