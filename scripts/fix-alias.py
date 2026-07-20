#!/usr/bin/env python3
"""Fix detectFileReferences function in repo-mapper.ts"""
import re

path = r'C:\Users\tqv11\.openclaw\workspace\super-agent-plugin\src\repo-mapper.ts'

with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

# Find function
start = content.find('export function detectFileReferences')
if start < 0:
    print("Function not found")
    exit(1)

# Find end (last })
depth = 0
end = start
for i in range(start, len(content)):
    if content[i] == '{': depth += 1
    elif content[i] == '}':
        depth -= 1
        if depth == 0:
            end = i + 1
            break

old_func = content[start:end]

new_func = """export function detectFileReferences(text: string): string[] {
  const results: string[] = [];

  const PREFIXES = 'src|app|lib|components|hooks|utils|config|types|pages|web|data|dist|public';
  const EXT = '(tsx|ts|jsx|json|js|css|sql)';
  const EXT_BARE = '(tsx|ts|jsx|json|js|css)';

  // Pattern 1: relative source paths (forward slash)
  const pat1 = new RegExp('(?:' + PREFIXES + ')[/\\\\\\\\][a-zA-Z0-9_\\\\\\-/.]+\\.' + EXT, 'g');
  results.push(...[...text.matchAll(pat1)].map(m => m[0].trim()));

  // Pattern 2: @/ alias paths
  const pat2 = /@\\/[a-zA-Z0-9_\\-/]+\\.(tsx|ts|jsx|json|js|css|sql)/g;
  results.push(...[...text.matchAll(pat2)].map(m => m[0].trim()));

  // Pattern 3: bare filename (quoted or bare)
  const pat3 = new RegExp('[\\\\"\\\\\\'`]?([a-zA-Z0-9_\\\\\\-]+\\.' + EXT_BARE + ')[\\\\"\\\\\\'`]?', 'g');
  results.push(...[...text.matchAll(pat3)].map(m => m[1].trim()));

  // Pattern 4: Windows backslash paths
  const pat4 = new RegExp('(?:' + PREFIXES + ')\\\\\\\\[a-zA-Z0-9_\\\\\\-/.]+\\.' + EXT, 'g');
  results.push(...[...text.matchAll(pat4)].map(m => m[0].trim().replace(/\\\\/g, '/')));

  return [...new Set(results.filter(Boolean))];
}"""

content = content[:start] + new_func + content[end:]

with open(path, 'w', encoding='utf-8') as f:
    f.write(content)

print("Replaced OK")
print()

# Verify the generated code by extracting and showing each pattern
with open(path, 'r', encoding='utf-8') as f:
    for line in f:
        if 'pat1 =' in line or 'pat2 =' in line or 'pat3 =' in line or 'pat4 =' in line:
            stripped = line.strip()
            print("  " + stripped)
