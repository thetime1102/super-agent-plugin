"""Create GitHub issues for all 8 bugs"""
import json, os, subprocess, sys

# Get token from git credential manager
result = subprocess.run(
    ["git", "credential", "fill"],
    input="protocol=https\nhost=github.com\n\n",
    capture_output=True, text=True, cwd=os.path.dirname(__file__)
)
token = None
for line in result.stdout.splitlines():
    if line.startswith("password="):
        token = line[9:]
        break

if not token:
    print("FAILED: No GitHub token found")
    sys.exit(1)

headers = {
    "Authorization": f"Bearer {token}",
    "Accept": "application/vnd.github+json",
}
api = "https://api.github.com/repos/thetime1102/super-agent-plugin/issues"

issues = [
    {
        "title": "Bug #1: openclaw.plugin.json missing from npm files array",
        "body": """**Severity:** CRITICAL
**File:** package.json → `files` array

## Description
The `files` array is `["dist/", "README.md", "LICENSE"]` — does NOT include `openclaw.plugin.json`. When published to npm, the manifest will be excluded from the package root.

## Impact
OpenClaw requires `openclaw.plugin.json` in the plugin root. Without it, the plugin is treated as "plugin error" and cannot load.

## Fix
Add `"openclaw.plugin.json"` to the `files` array in `package.json`.""",
        "labels": ["bug", "critical"]
    },
    {
        "title": "Bug #2: WASM_DIR resolve fails in non-dev contexts",
        "body": """**Severity:** CRITICAL
**File:** `src/repo-mapper.ts` → `ensureParser()`

## Description
`WASM_DIR = __dirname` (dist/ folder). When installed via npm/ClawHub, the plugin may be symlinked into a managed project. The original code had only one WASM path + one node_modules fallback, which fails when the directory structure differs.

## Fix
Add a `WASM_PATHS` array with 3 fallback locations: dist/, plugin-root/dist/, and node_modules/web-tree-sitter/. Retry WASM init 3 times with exponential backoff.""",
        "labels": ["bug", "critical"]
    },
    {
        "title": "Bug #3: Context engine 'super-agent' not declared in manifest contracts",
        "body": """**Severity:** HIGH
**File:** `openclaw.plugin.json`

## Description
The manifest has `contracts.tools` but lacks `contracts.contextEngines`. The context engine is registered at runtime but not declared statically. OpenClaw cannot discover engine ownership without loading the plugin.

## Fix
Add `"contextEngines": ["super-agent"]` to `contracts`.""",
        "labels": ["bug"]
    },
    {
        "title": "Bug #4: ensureParser() has no retry mechanism on WASM init failure",
        "body": """**Severity:** HIGH
**File:** `src/repo-mapper.ts` → `ensureParser()`

## Description
If `Parser.init()` or `Language.load()` fails (ENOENT, OOM, corrupted WASM), the `_initPromise` stays rejected permanently. `_parser` stays `null` forever — no recovery possible without plugin reload.

## Fix
Add retry loop (3 attempts) with exponential backoff (1s, 2s, 4s). Clear `_initPromise` on final failure so next caller retries.""",
        "labels": ["bug"]
    },
    {
        "title": "Bug #5: Root path resolution fails for project-relative file paths",
        "body": """**Severity:** CRITICAL
**File:** `src/index.ts` → `execute()` + `assemble()`

## Description
`process.env.INIT_CWD || process.cwd()` returns the OpenClaw workspace root (`C:/Users/.../.openclaw/workspace`), NOT the project root. File paths like `src/services/file.ts` resolve to wrong location:
- Current: `workspace/src/services/file.ts` ❌
- Expected: `workspace/nhatvi-ecosystem-dev/src/services/file.ts` ✅

## Fix
- Add `projectRoot` to plugin `configSchema`
- Fallback: auto-detect project subdirectory by scanning for common dirs (src/, app/, package.json)
- Pass config path to tool + context engine via closure

## Reproduction
```typescript
const rootDir = process.cwd(); // workspace root!
const fullPath = join(rootDir, "src/services/llm.service.ts");
// => C:/.../.openclaw/workspace/src/services/llm.service.ts (NOT FOUND)
```""",
        "labels": ["bug", "critical"]
    },
    {
        "title": "Bug #6: detectFileReferences regex too narrow for Next.js paths",
        "body": """**Severity:** HIGH
**File:** `src/repo-mapper.ts` → `detectFileReferences()`

## Description
The regex `/(?:src|web|data|dist|public)\/.../` only matches paths starting with these 5 prefixes. Common Next.js project paths are MISSING:
- `app/api/route.ts` ❌
- `lib/utils.ts` ❌
- `components/Button.tsx` ❌
- `hooks/useAuth.ts` ❌
- `config/site.ts` ❌

## Fix
Expand regex to include: `app`, `lib`, `components`, `hooks`, `utils`, `config`, `types`, and optional `@/` path alias.""",
        "labels": ["bug"]
    },
    {
        "title": "Bug #7: ConfigSchema is empty — no projectRoot configuration",
        "body": """**Severity:** MEDIUM
**File:** `openclaw.plugin.json` → `configSchema`

## Description
`configSchema` is `{"type":"object","properties":{},"additionalProperties":false}` — empty. Users cannot configure the plugin (e.g., set `projectRoot`).

## Fix
Add `projectRoot` as an optional string property in `configSchema`. Read it via `api.pluginConfig` in `register()` and pass to tool/engine.""",
        "labels": ["bug"]
    },
    {
        "title": "Bug #8: No Windows backslash path support in detectFileReferences",
        "body": """**Severity:** MEDIUM
**File:** `src/repo-mapper.ts` → `detectFileReferences()`

## Description
The regex only matches forward-slash paths (`src/file.ts`). On Windows, users may type paths with backslashes (`src\\file.ts`), which are not detected.

## Fix
Add a regex variant that matches backslash-separated paths. Normalize to forward slashes before processing.""",
        "labels": ["bug"]
    },
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
    if "html_url" in resp:
        print(f"✅ {issue['title']}: {resp['html_url']}")
    else:
        print(f"❌ {issue['title']}: {resp.get('message', 'unknown error')}")
