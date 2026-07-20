// Exact reproduction of detectFileReferences logic from dist
import('../dist/repo-mapper.js').then(m => {
  const text = 'Import @/components/Button.tsx';
  const results = [];
  const PREFIXES = 'src|app|lib|components|hooks|utils|config|types|pages|web|data|dist|public';
  const EXT = '(tsx|ts|jsx|json|js|css|sql)';
  const EXT_BARE = '(tsx|ts|jsx|json|js|css)';

  // EXACT regex from dist file (copy-pasted)
  const pat1 = new RegExp('(?:' + PREFIXES + ')[/\\\\][a-zA-Z0-9_\\\-/.]+\.' + EXT, 'g');
  results.push(...[...text.matchAll(pat1)].map(m => m[0].trim()));

  const pat2 = /@\/[a-zA-Z0-9_\-/]+\.(tsx|ts|jsx|json|js|css|sql)/g;
  results.push(...[...text.matchAll(pat2)].map(m => m[0].trim()));

  const pat3 = new RegExp('["\\\'`]?([a-zA-Z0-9_\\\-]+\.' + EXT_BARE + ')["\\\'`]?', 'g');
  results.push(...[...text.matchAll(pat3)].map(m => m[1].trim()));

  const pat4 = new RegExp('(?:' + PREFIXES + ')\\\\[a-zA-Z0-9_\\\-/.]+\.' + EXT, 'g');
  results.push(...[...text.matchAll(pat4)].map(m => m[0].trim().replace(/\\/g, '/')));

  const final = [...new Set(results.filter(Boolean))];
  console.log('Final:', JSON.stringify(final));
  console.log('\nCompare with dist detectFileReferences:');
  console.log(JSON.stringify(m.detectFileReferences(text)));
}).catch(e => console.error('ERR:', e));
