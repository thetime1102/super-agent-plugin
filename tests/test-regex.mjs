// Verify regex patterns work correctly after fix
const prefixes = 'src|app|lib|components|hooks|utils|config|types|pages|web|data|dist|public';
// json before js so .json doesn't match as .js
const p1 = new RegExp('(?:' + prefixes + ')[/\\\\][a-zA-Z0-9_\\-/.]+\.(tsx|ts|jsx|json|js|css|sql)', 'g');
const p2 = /@\/[a-zA-Z0-9_\-/.]+\.(tsx|ts|jsx|js)/g;
const p3 = /["'`]?([a-zA-Z0-9_\-]+\.(tsx|ts|jsx|js|css|json))["'`]?/g;

function detect(text) {
  const m1 = text.match(p1) || [];
  const m2 = text.match(p2) || [];
  const m3 = [...text.matchAll(p3)].map(m => m[1]).filter(Boolean);
  return [...new Set([...m1, ...m2, ...m3])];
}

const tests = [
  ['components/', 'Fix components/Button.tsx', ['components/Button.tsx']],
  ['pages/', 'Check pages/index.tsx', ['pages/index.tsx']],
  ['@/ alias', 'Import @/components/Button.tsx', ['@/components/Button.tsx', 'components/Button.tsx']],
  ['.json', 'Update data/config.json', ['data/config.json']],
  ['.sql', 'Review migrations/001.sql', []],
  ['.css', 'Update src/styles/main.css', ['src/styles/main.css']],
  ['bare .ts', 'Check llm.service.ts', []], // multi-dot not supported
  ['multi files', 'Fix src/a.ts and lib/b.ts', ['src/a.ts', 'lib/b.ts']],
  ['empty', 'No file ref', []],
];

let ok = 0, fail = 0;
for (const [name, input, expected] of tests) {
  const result = detect(input);
  const pass = JSON.stringify(result.sort()) === JSON.stringify([...expected].sort());
  console.log((pass ? 'PASS' : 'FAIL') + ' ' + name + ': ' + input + ' => ' + JSON.stringify(result));
  if (pass) ok++; else fail++;
}
console.log('\n' + ok + ' passed, ' + fail + ' failed');
process.exit(fail > 0 ? 1 : 0);
