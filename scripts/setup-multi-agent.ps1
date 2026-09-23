#Requires -Version 5.1
<#
.SYNOPSIS
  Preview safe per-skill links through the portable Bash installer.
.DESCRIPTION
  No changes by default. Pass --apply explicitly. Existing paths are preserved.
  On Windows use WSL with this product on its POSIX filesystem for skill links.
  Native Windows file-based use does not require installing discovery links.
#>
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$entry = Join-Path $repoRoot 'skills/legends-obsidian/SKILL.md'
if ($env:OS -eq 'Windows_NT') {
    Write-Output 'Native Windows: no host paths changed.'
    Write-Output "For Grok, Codex, Gemini, Claude, Cursor or MetaMuse, ask the agent to read: $entry"
    Write-Output 'For automatic links, run scripts/setup-multi-agent.sh in WSL against a POSIX product copy.'
    Write-Output 'Use --dry-run first, then --apply. MetaMuse uses a manual fallback.'
    exit 0
}
$bash = Get-Command bash -ErrorAction Stop
& $bash.Source (Join-Path $repoRoot 'scripts/setup-multi-agent.sh') @args
exit $LASTEXITCODE
