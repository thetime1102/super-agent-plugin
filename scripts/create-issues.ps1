#Requires -Version 7
$ErrorActionPreference = "Stop"

function New-GitHubIssue {
    param($Title, $Body, $Labels)
    
    $tokenOutput = "protocol=https`nhost=github.com`n" | git credential fill 2>&1
    $token = ($tokenOutput | Select-String 'password=(.*)').Matches[0].Groups[1].Value
    
    $headers = @{
        Authorization = "Bearer $token"
        Accept = "application/vnd.github+json"
    }
    
    $bodyObj = @{
        title = $Title
        body = $Body
        labels = $Labels
    } | ConvertTo-Json -Depth 3
    
    try {
        $resp = Invoke-RestMethod -Uri "https://api.github.com/repos/thetime1102/super-agent-plugin/issues" `
            -Method POST -Headers $headers -Body $bodyObj -ContentType "application/json"
        Write-Output "✅ $Title"
        Write-Output "   $($resp.html_url)"
    } catch {
        Write-Output "❌ $Title"
        Write-Output "   $($_.Exception.Message)"
    }
}

$bug1 = @"
**Severity:** CRITICAL
**File:** package.json - files array

The files array is `["dist/", "README.md", "LICENSE"]` - does NOT include `openclaw.plugin.json`. When published to npm, the manifest will be excluded from the package root.

OpenClaw requires openclaw.plugin.json in the plugin root. Without it, the plugin is treated as "plugin error" and cannot load.

Fix: Add `"openclaw.plugin.json"` to the files array.
"@

New-GitHubIssue -Title "Bug #1: openclaw.plugin.json missing from npm files array" -Body $bug1 -Labels @("bug","critical")

$bug2 = @"
**Severity:** CRITICAL
**File:** src/repo-mapper.ts - ensureParser()

WASM_DIR = __dirname (dist/ folder). When installed via npm/ClawHub, the plugin may be symlinked into a managed project. The original code had only one WASM path + one node_modules fallback, which fails when the directory structure differs.

Fix: Add a WASM_PATHS array with 3 fallback locations: dist/, plugin-root/dist/, and node_modules/web-tree-sitter/. Retry WASM init 3 times with exponential backoff.
"@

New-GitHubIssue -Title "Bug #2: WASM_DIR resolve fails in non-dev contexts" -Body $bug2 -Labels @("bug","critical")

$bug3 = @"
**Severity:** HIGH
**File:** openclaw.plugin.json

The manifest has contracts.tools but lacks contracts.contextEngines. The context engine is registered at runtime but not declared statically. OpenClaw cannot discover engine ownership without loading the plugin.

Fix: Add "contextEngines": ["super-agent"] to contracts.
"@

New-GitHubIssue -Title "Bug #3: Context engine 'super-agent' not declared in manifest contracts" -Body $bug3 -Labels @("bug")

$bug4 = @"
**Severity:** HIGH
**File:** src/repo-mapper.ts - ensureParser()

If Parser.init() or Language.load() fails (ENOENT, OOM, corrupted WASM), the _initPromise stays rejected permanently. _parser stays null forever - no recovery possible without plugin reload.

Fix: Add retry loop (3 attempts) with exponential backoff (1s, 2s, 4s). Clear _initPromise on final failure so next caller retries.
"@

New-GitHubIssue -Title "Bug #4: ensureParser() has no retry mechanism on WASM init failure" -Body $bug4 -Labels @("bug")

$bug5 = @"
**Severity:** CRITICAL
**File:** src/index.ts - execute() + assemble()

process.env.INIT_CWD || process.cwd() returns the OpenClaw workspace root, NOT the project root. File paths like src/services/file.ts resolve to wrong location.

Expected: workspace/nhatvi-ecosystem-dev/src/services/file.ts
Actual:   workspace/src/services/file.ts (file not found)

Fix: Add projectRoot to plugin configSchema. Fallback: auto-detect project subdirectory by scanning for common dirs. Pass config path to tool + context engine via closure.
"@

New-GitHubIssue -Title "Bug #5: Root path resolution fails for project-relative file paths" -Body $bug5 -Labels @("bug","critical")

$bug6 = @"
**Severity:** HIGH
**File:** src/repo-mapper.ts - detectFileReferences()

The regex `/(?:src|web|data|dist|public)\/.../` only matches paths starting with these 5 prefixes. Common Next.js paths are MISSING:

- app/api/route.ts
- lib/utils.ts
- components/Button.tsx
- hooks/useAuth.ts
- config/site.ts
- types/index.ts

Fix: Expand regex to include app, lib, components, hooks, utils, config, types, and optional @/ prefix.
"@

New-GitHubIssue -Title "Bug #6: detectFileReferences regex too narrow for Next.js paths" -Body $bug6 -Labels @("bug")

$bug7 = @"
**Severity:** MEDIUM
**File:** openclaw.plugin.json - configSchema

configSchema is {"type":"object","properties":{},"additionalProperties":false} - empty. Users cannot configure the plugin (e.g., set projectRoot).

Fix: Add projectRoot as an optional string property in configSchema. Read it via api.pluginConfig in register() and pass to tool/engine.
"@

New-GitHubIssue -Title "Bug #7: ConfigSchema is empty - no projectRoot configuration" -Body $bug7 -Labels @("bug")

$bug8 = @"
**Severity:** MEDIUM
**File:** src/repo-mapper.ts - detectFileReferences()

The regex only matches forward-slash paths (src/file.ts). On Windows, users may type paths with backslashes (src\file.ts), which are not detected.

Fix: Add a regex variant that matches backslash-separated paths. Normalize to forward slashes before processing.
"@

New-GitHubIssue -Title "Bug #8: No Windows backslash path support in detectFileReferences" -Body $bug8 -Labels @("bug")
