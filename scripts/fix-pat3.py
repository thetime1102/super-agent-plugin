import sys
path = r'C:\Users\tqv11\.openclaw\workspace\super-agent-plugin\dist\repo-mapper.js'
with open(path, 'r', encoding='utf-8') as f:
    c = f.read()

# Fix the ending quote group: remove the ? after ] at the end of pat3
old = """`]?', 'g');"""
new = """`]', 'g');"""
c = c.replace(old, new)

with open(path, 'w', encoding='utf-8') as f:
    f.write(c)
print('Fixed ending quote')
