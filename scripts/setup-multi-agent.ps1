#Requires -Version 5.1
<#
.SYNOPSIS
  Legends Obsidian — multi-agent skill installer (Windows).

.DESCRIPTION
  Junctions skills into Grok, Codex, Claude, Gemini, OpenCode, Cursor, Windsurf.
  Idempotent. Safe to re-run. Does not delete host-owned non-junction paths.

.EXAMPLE
  pwsh -File E:\legends-obsidian\bin\setup-multi-agent.ps1
#>

$ErrorActionPreference = 'Stop'
# This script lives in <repo>/bin/
$RepoRoot = if ($PSScriptRoot) { Split-Path -Parent $PSScriptRoot } else { 'E:\legends-obsidian' }
$SkillsDir = Join-Path $RepoRoot 'skills'
if (-not (Test-Path (Join-Path $SkillsDir 'wiki\SKILL.md'))) {
  throw "Skills dir missing or incomplete: $SkillsDir"
}

function Write-Info($m) { Write-Host $m -ForegroundColor Cyan }
function Write-Ok($m) { Write-Host $m -ForegroundColor Green }
function Write-Skip($m) { Write-Host $m -ForegroundColor DarkGray }
function Write-Warn($m) { Write-Host $m -ForegroundColor Yellow }

function Set-Junction {
  param(
    [Parameter(Mandatory)][string]$LinkPath,
    [Parameter(Mandatory)][string]$TargetPath,
    [Parameter(Mandatory)][string]$AgentName
  )
  if (-not (Test-Path -LiteralPath $TargetPath)) {
    Write-Warn "[$AgentName] target missing: $TargetPath"
    return
  }
  $parent = Split-Path -Parent $LinkPath
  if (-not (Test-Path $parent)) {
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
  }

  if (Test-Path -LiteralPath $LinkPath) {
    $item = Get-Item -LiteralPath $LinkPath -Force
    $isReparse = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
    if ($isReparse -or $item.LinkType -eq 'Junction' -or $item.LinkType -eq 'SymbolicLink') {
      $current = $null
      try { $current = $item.Target } catch {}
      $currentStr = if ($current -is [array]) { $current -join ';' } else { [string]$current }
      $normTarget = (Resolve-Path -LiteralPath $TargetPath).Path
      if ($currentStr -and ($currentStr -replace '/', '\').TrimEnd('\') -eq ($normTarget -replace '/', '\').TrimEnd('\')) {
        Write-Skip "[$AgentName] already linked: $LinkPath"
        return
      }
      # relink
      cmd /c "rmdir `"$LinkPath`"" | Out-Null
    } else {
      Write-Warn "[$AgentName] path exists and is not a junction (skip): $LinkPath"
      return
    }
  }

  $null = cmd /c "mklink /J `"$LinkPath`" `"$TargetPath`""
  if (Test-Path -LiteralPath $LinkPath) {
    Write-Ok "[$AgentName] linked: $LinkPath -> $TargetPath"
  } else {
    Write-Warn "[$AgentName] mklink failed: $LinkPath"
  }
}

Write-Info "Legends Obsidian multi-agent installer"
Write-Info "Repo: $RepoRoot"
Write-Host ""

$skillNames = Get-ChildItem -LiteralPath $SkillsDir -Directory | ForEach-Object { $_.Name }

# --- Per-skill hosts (Grok, Codex, Claude prefer flat skill dirs) ---
$perSkillHomes = @(
  @{ Name = 'Grok';   Root = (Join-Path $env:USERPROFILE '.grok\skills') },
  @{ Name = 'Codex';  Root = (Join-Path $env:USERPROFILE '.codex\skills') },
  @{ Name = 'Claude'; Root = (Join-Path $env:USERPROFILE '.claude\skills') }
)

foreach ($agentHome in $perSkillHomes) {
  foreach ($s in $skillNames) {
    Set-Junction -LinkPath (Join-Path $agentHome.Root $s) -TargetPath (Join-Path $SkillsDir $s) -AgentName $agentHome.Name
  }
}

# --- Pack-style hosts (one folder = whole skills tree) ---
$packHomes = @(
  @{ Name = 'Gemini';   Link = (Join-Path $env:USERPROFILE '.gemini\skills\legends-obsidian') },
  @{ Name = 'OpenCode'; Link = (Join-Path $env:USERPROFILE '.opencode\skills\legends-obsidian') },
  @{ Name = 'Agents';   Link = (Join-Path $env:USERPROFILE '.agents\skills\legends-obsidian') }
)
foreach ($p in $packHomes) {
  Set-Junction -LinkPath $p.Link -TargetPath $SkillsDir -AgentName $p.Name
}

# --- Workspace-local ---
Set-Junction -LinkPath (Join-Path $RepoRoot '.cursor\skills') -TargetPath $SkillsDir -AgentName 'Cursor'
Set-Junction -LinkPath (Join-Path $RepoRoot '.windsurf\skills') -TargetPath $SkillsDir -AgentName 'Windsurf'
Set-Junction -LinkPath (Join-Path $RepoRoot '.claude\skills') -TargetPath $SkillsDir -AgentName 'Claude-project'
Set-Junction -LinkPath (Join-Path $RepoRoot '.grok\skills') -TargetPath $SkillsDir -AgentName 'Grok-project'

# Decommission old brand skill dirs if present (do not reinstall codex-obsidian)
foreach ($dead in @(
  (Join-Path $env:USERPROFILE '.grok\skills\codex-obsidian'),
  (Join-Path $env:USERPROFILE '.codex\skills\codex-obsidian'),
  (Join-Path $env:USERPROFILE '.codex\skills\codex-obsidian-pack'),
  (Join-Path $env:USERPROFILE '.gemini\skills\codex-obsidian'),
  (Join-Path $env:USERPROFILE '.opencode\skills\codex-obsidian')
)) {
  if (-not (Test-Path -LiteralPath $dead)) { continue }
  $item = Get-Item -LiteralPath $dead -Force
  $isReparse = [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
  if ($isReparse) {
    cmd /c "rmdir `"$dead`"" | Out-Null
    Write-Ok "[decommission] removed junction $dead"
  } else {
    Write-Warn "[decommission] real dir left in place (remove manually if desired): $dead"
  }
}

Write-Host ""
Write-Ok "Done."
Write-Host "Brand: Legends Obsidian only (Codex Obsidian skill name decommissioned)"
Write-Host "Skills: $($skillNames.Count) from $SkillsDir"
Write-Host "Global: ~/.grok/skills + ~/.codex/skills (any working directory)"
Write-Host ""
Write-Host "Verify:"
Write-Host "  Grok:   say 'Legends Obsidian'"
Write-Host "  Codex:  /wiki or skill list"
Write-Host "  Claude: skills list / Legends Obsidian"
Write-Host "  Gemini: open project + GEMINI.md"
Write-Host "Matrix: docs/AGENTS-MATRIX.md"
