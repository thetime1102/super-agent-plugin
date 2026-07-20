// Debug detectFileReferences step by step
import('../dist/repo-mapper.js').then(m => {
  const text = 'Import @/components/Button.tsx';
  
  // Monkey-patch to trace
  const results = [];
  const PREFIXES = 'src|app|lib|components|hooks|utils|config|types|pages|web|data|dist|public';
  const EXT = '(tsx|ts|jsx|json|js|css|sql)';
  const EXT_BARE = '(tsx|ts|jsx|json|js|css)';

  const pat1 = new RegExp('(?:' + PREFIXES + ')[/\\\\][a-zA-Z0-9_\\\\\\-/.]+\\.' + EXT, 'g');
  const r1 = [...text.matchAll(pat1)].map(m => m[0].trim());
  console.log('After pat1:', r1);
  
  const pat2 = /@\/[a-zA-Z0-9_\-/]+\.(tsx|ts|jsx|json|js|css|sql)/g;
  const r2 = [...text.matchAll(pat2)].map(m => m[0].trim());
  console.log('After pat2:', r2);

  const quotePart = "[" + '"' + "'" + "`" + "]?";
  const namePart = "([a-zA-Z0-9_\\\\-]+\\.(" + EXT_BARE + "))";
  const pat3 = new RegExp(quotePart + namePart + quotePart, 'g');
  const r3 = [...text.matchAll(pat3)].map(m => m[1].trim());
  console.log('After pat3:', r3);

  const pat4 = new RegExp('(?:' + PREFIXES + ')\\\[a-zA-Z0-9_\\\\\\-/.]+\\.' + EXT, 'g');
  const r4 = [...text.matchAll(pat4)].map(m => m[0].trim().replace(/\\/g, '/'));
  console.log('After pat4:', r4);

  const all = [...new Set([...r1, ...r2, ...r3, ...r4].filter(Boolean))];
  console.log('Final:', all);
  
  console.log('\nActual detectFileReferences:');
  console.log(m.detectFileReferences(text));
}).catch(e => console.error('ERR:', e));
