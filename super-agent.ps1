<#
.SYNOPSIS
    NHAT VI CAKE Super Agent — Auto-Indexing & Semantic Memory Tool

.DESCRIPTION
    Unified CLI for managing semantic memory using sqlite-memory.
    
    COMMANDS:
      index <path>       — Index file or directory
      watch [dir]        — 🔥 Cách 1: Watch directory for changes (auto-index on save)
      git-index          — 🔥 Cách 2: Index changed files from git diff
      search <query>     — Search indexed memory
      status             — Show memory stats
      clean              — Remove stale entries for deleted files
      daemon             — Start background watcher (hidden window)

    FLAGS:
      --bg               — Run watch in background (detached, hidden window)
      --context <label>  — Memory context label (default: workspace|code:dev)

    EXAMPLES:
      super-agent watch                        # Watch nhatvi-ecosystem-dev
      super-agent watch --bg                   # Watch in background
      super-agent watch C:\path\to\project     # Watch custom directory
      super-agent git-index                    # Index HEAD changes
      super-agent search "auto post"           # Semantic search
      super-agent status                       # Stats
#>

param(
    [Parameter(Position=0)]
    [string]$Command = "help",

    [Parameter(Position=1, ValueFromRemainingArguments=$true)]
    [string[]]$Arguments = @()
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# Find Python
$PythonCandidates = @(
    "$env:LOCALAPPDATA\Python\pythoncore-3.14-64\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    (Get-Command python -ErrorAction SilentlyContinue).Source
)

$PythonExe = $null
foreach ($p in $PythonCandidates) {
    if ($p -and (Test-Path $p)) {
        $PythonExe = $p
        break
    }
}

if (-not $PythonExe) {
    Write-Error "❌ Python not found!"
    exit 1
}

$PyScript = Join-Path $ScriptDir "super_agent.py"

function Show-Help {
    $helpText = @"

🧠 Super Agent — NHAT VI CAKE Semantic Memory Engine
=====================================================

USAGE:
    super-agent <command> [args]

COMMANDS:
    index <path>         Index a file or directory
                         e.g. super-agent index src/services/token-budget.ts
                              super-agent index src/ --context code:dev

    watch [dir]          🔥 Cách 1: Watch & auto-index on save
                         Auto-indexes .ts, .tsx, .js, .py, etc. on file change
                         e.g. super-agent watch
                              super-agent watch --bg
                              super-agent watch C:\Projects\my-app

    git-index            🔥 Cách 2: Git incremental index
                         Indexes files changed in last commit
                         e.g. super-agent git-index
                              super-agent git-index HEAD~3

    search <query>       Semantic search in memory
                         e.g. super-agent search "token budget"

    status               Show memory statistics and recent files

    clean                Remove stale entries (deleted/moved files)

    daemon               Start background watcher (hidden window)

FLAGS:
    --bg                 Background mode (for watch)
    --context <label>    Context label (default: workspace or code:dev)

"@
    Write-Host $helpText
}

switch ($Command.ToLower()) {
    "help" { Show-Help }
    "-h" { Show-Help }
    "--help" { Show-Help }

    "daemon" {
        & $PythonExe $PyScript daemon
    }

    "watch" {
        $bg = $false
        $watchDir = ""
        $remaining = @()
        $contextSet = $false

        for ($i = 0; $i -lt $Arguments.Count; $i++) {
            $arg = $Arguments[$i]
            if ($arg -eq "--bg" -or $arg -eq "-bg") { $bg = $true; continue }
            if ($arg -eq "--context" -or $arg -eq "-context") {
                $i++
                if ($i -lt $Arguments.Count) { 
                    $remaining += "--context"; $remaining += $Arguments[$i]
                    $contextSet = $true
                }
                continue
            }
            if ($arg -match "^-") { $remaining += $arg; continue }
            if (-not $watchDir) { $watchDir = $arg; continue }
            $remaining += $arg
        }

        if ($bg) {
            # Background: spawn hidden PowerShell
            $watchArg = if ($watchDir) { " '$watchDir'" } else { "" }
            $extraArgs = if ($remaining.Count -gt 0) { " $($remaining -join ' ')" } else { "" }
            $psCmd = "& '$PSCommandPath' daemon"
            $psi = New-Object System.Diagnostics.ProcessStartInfo
            $psi.FileName = "powershell.exe"
            $psi.Arguments = "-NoProfile -WindowStyle Hidden -Command $psCmd"
            $psi.UseShellExecute = $false
            $psi.CreateNoWindow = $true
            [System.Diagnostics.Process]::Start($psi) | Out-Null
            Write-Host "👀 Super Agent watcher started in background (hidden window)"
            Write-Host "   To stop: taskkill /f /im python.exe (last resort)"
            Write-Host "   Or restart your machine"
        } else {
            if ($watchDir) {
                & $PythonExe $PyScript watch $watchDir $remaining
            } else {
                & $PythonExe $PyScript watch @remaining
            }
        }
    }

    "git-index" {
        $revision = "HEAD"
        $remaining = @()
        for ($i = 0; $i -lt $Arguments.Count; $i++) {
            $arg = $Arguments[$i]
            if ($arg -match "^[a-zA-Z0-9_~^@]+$" -and $arg -notlike "-*") {
                $revision = $arg
            } else {
                $remaining += $arg
            }
        }
        & $PythonExe $PyScript git-index $revision $remaining
    }

    "index" {
        if ($Arguments.Count -eq 0) {
            Write-Error "❌ Usage: super-agent index <path> [--context <label>]"
            exit 1
        }
        & $PythonExe $PyScript index $Arguments
    }

    "search" {
        if ($Arguments.Count -eq 0) {
            Write-Error "❌ Usage: super-agent search <query> [limit]"
            exit 1
        }
        & $PythonExe $PyScript search $Arguments
    }

    "status" {
        & $PythonExe $PyScript status
    }

    "clean" {
        & $PythonExe $PyScript clean
    }

    default {
        Write-Host "❌ Unknown command: $Command"
        Write-Host ""
        Show-Help
        exit 1
    }
}
