import sys, json, subprocess

r = subprocess.run(['git', 'credential', 'fill'],
    input='protocol=https\nhost=github.com\n\n',
    capture_output=True, text=True)
token = None
for line in r.stdout.splitlines():
    if line.startswith('password='):
        token = line[9:]

import urllib.request
req = urllib.request.Request(
    'https://api.github.com/repos/thetime1102/super-agent-plugin/issues?state=open&per_page=15',
    headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json'})
resp = json.loads(urllib.request.urlopen(req).read())
for issue in resp:
    print(f'#{issue["number"]}: {issue["title"]}')
