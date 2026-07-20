import json, subprocess, urllib.request

r = subprocess.run(['git', 'credential', 'fill'],
    input='protocol=https\nhost=github.com\n\n',
    capture_output=True, text=True)
token = None
for line in r.stdout.splitlines():
    if line.startswith('password='):
        token = line[9:]

api = 'https://api.github.com/repos/thetime1102/super-agent-plugin/issues'
headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'}

issues = [
    {'title': 'Bug #10: Context engine silently swallows mapFile errors',
     'body': '**Severity:** LOW\n**File:** src/index.ts - assemble()\n\ncatch { /* skip */ } hides all errors (WASM not ready, file not found).\n\nFix: Log the error before skipping.',
     'labels': ['bug']},
    {'title': 'Bug #11: detectFileReferences returns false positive bare words',
     'body': '**Severity:** LOW\n**File:** src/repo-mapper.ts\n\nPattern 3 (bare filename regex) can match words without extension.\n\nFix: Tighten regex to require path prefix or quoting.',
     'labels': ['bug']},
]

for issue in issues:
    req = urllib.request.Request(api, data=json.dumps(issue).encode(), headers=headers, method='POST')
    resp = json.loads(urllib.request.urlopen(req).read())
    url = resp.get('html_url')
    if url:
        print(f'OK #{resp["number"]}: {url}')
    else:
        print(f'FAIL: {resp.get("message", resp)}')
