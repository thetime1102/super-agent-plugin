// Ultra simple test - just pat3 on the exact input
const text = 'Import @/components/Button.tsx';
const EXT_BARE = '(tsx|ts|jsx|json|js|css)';

// EXACT regex from dist file 
const pat3 = new RegExp('["\\\'`]?([a-zA-Z0-9_\\\-]+\.' + EXT_BARE + ')["\\\'`]?', 'g');

let match;
let count = 0;
while ((match = pat3.exec(text)) !== null) {
  count++;
  console.log(`Match ${count}: "${match[1]}" at index ${match.index}`);
}

console.log(`\nTotal matches: ${count}`);
console.log(`Global flag: ${pat3.global}`);
console.log(`Source: ${pat3.source}`);
