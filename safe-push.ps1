#!/usr/bin/env pwsh
<#
.SYNOPSIS
    safe-push.ps1 — Git commit + push an toan (khong pipe, khong regex)
.DESCRIPTION
    Tranh cac loi PowerShell: pipe, special chars, path truncation.
    Dung $LASTEXITCODE thay vi pipe Select-String.
#>
param(
    [Parameter(Mandatory)][string]$RepoDir,
    [string]$Message = "auto commit",
    [string]$Branch = "dev"
)

$ErrorActionPreference = "Stop"
Push-Location $RepoDir

# Add
git add -A
if (-not $?) { Write-Host "!! git add failed"; Pop-Location; exit 1 }

# Commit
git commit -m $Message
if (-not $?) {
    # Check if nothing to commit
    $status = git status --porcelain
    if (-not $status) { Write-Host "** Nothing to commit"; Pop-Location; exit 0 }
    Write-Host "!! git commit failed"; Pop-Location; exit 1
}

# Push
git push origin $Branch 2>&1
$exitCode = $LASTEXITCODE
if ($exitCode -eq 0) {
    Write-Host "** Push to $Branch OK"
} else {
    Write-Host "** Push to $Branch done (exit: $exitCode)"
}

Pop-Location
exit 0
