// Test Bug #11 fix
const { detectFileReferences } = await import('../dist/repo-mapper.js');

console.log('Unquoted components:', JSON.stringify(detectFileReferences('Import @/components/Button.tsx')));
console.log('Unquoted src:', JSON.stringify(detectFileReferences('Check src/services/llm.service.ts')));
console.log('Quoted:', JSON.stringify(detectFileReferences('Check "llm.service.ts" and `test.ts` and `Button.tsx`')));
console.log('Mixed:', JSON.stringify(detectFileReferences('Fix src/a.ts and "b.tsx" and components/c.tsx')));
console.log('No file:', JSON.stringify(detectFileReferences('Just words no paths')));
