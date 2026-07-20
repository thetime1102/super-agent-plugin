#!/usr/bin/env python3
"""Create GitHub Issues #9, #10, #11"""
import json, subprocess, sys

# Get token
r = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n\n",
                   capture_output=True, text=True, cwd=r"C:\Users\tqv11\.openclaw\workspace\super-agent-plugin")
token = None
for line in r.stdout.splitlines():
    if line.startswith("password="):
        token = line[9:]
        break

if not token:
    print("No token found")
    sys.exit(1)

api = "https://api.github.com/repos/thetime1102/super-agent-plugin/issues"

issues = [
    {
        "title": "Bug #9: projectRoot cached once in register() closure - fails in multi-session",
        "body": "**Severity:** MEDIUM\n**File:** src/index.ts - register()\n\nresolveProjectRoot() runs ONCE during register() and is captured in closure. If CWD differs between sessions, file resolution is wrong.\n\nFix: Move resolveProjectRoot() inside execute() and assemble() so it resolves per-call.",
        "labels": ["bug"]
    },
    {
        "title": "Bug #10: Context engine silently swallows mapFile errors",
        "body": "**Severity:** LOW\n**File:** src/index.ts - assemble()\n\ncatch { /* skip */ } hides all errors (WASM not ready, file not found, permission denied). User/LLM never knows why nothing was injected.\n\nFix: Use api.logger.warn() to log the error before skipping.",
        "labels": ["bug"]
    },
    {
        "title": "Bug #11: detectFileReferences returns false positive bare words",
        "body": "**Severity:** LOW\n**File:** src/repo-mapper.ts - detectFileReferences()\n\nPattern 3 (bare filename regex) can match words without extension in edge cases. E.g. 'components' from '@/components/Button.tsx' appears as a standalone false positive.\n\nFix: Tighten the bare filename regex to require path prefix or quoting.",
        "labels": ["bug"]
    }
]

for issue in issues:
    resp = json.loads(subprocess.run(
        ["curl", "-s", "-X", "POST", api,
         "-H", f"Authorization: Bearer {token}",
         "-H", "Accept: application/vnd.github+json",
         "-H", "Content-Type: application/json",
         "-d", json.dumps(issue)],
        capture_output=True, text=True
    ).stdout)
    url = resp.get("html_url")
    if url:
        print(f"✅ {issue['title']}: {url}")
    else:
        print(f"❌ {issue['title']}: {resp.get('message', resp)}")
