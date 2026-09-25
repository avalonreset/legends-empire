param(
  [string]$CodexHome = $(if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $HOME ".codex" }),
  [switch]$DryRun
)

$ErrorActionPreference = "Stop"

$skillNames = @(
  "codex-empire",
  "wiki",
  "wiki-ingest",
  "wiki-query",
  "wiki-lint",
  "wiki-fold",
  "save",
  "autoresearch",
  "canvas",
  "defuddle",
  "obsidian-markdown",
  "obsidian-bases"
)

$sourceRoot = Join-Path $CodexHome "skills"
$targetRoot = Join-Path $CodexHome "agents\skills"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupRoot = Join-Path $CodexHome "agents-skills-backup-codex-empire-$timestamp"

if (-not (Test-Path -LiteralPath $sourceRoot)) {
  throw "Source skills directory not found: $sourceRoot"
}

$missing = @()
foreach ($name in $skillNames) {
  $skillFile = Join-Path $sourceRoot "$name\SKILL.md"
  if (-not (Test-Path -LiteralPath $skillFile)) {
    $missing += $name
  }
}

if ($missing.Count -gt 0) {
  throw "Missing installed source skills: $($missing -join ', ')"
}

Write-Output "Source: $sourceRoot"
Write-Output "Target: $targetRoot"
if ($DryRun) {
  Write-Output "Dry run: no files will be changed."
}

if (-not $DryRun) {
  New-Item -ItemType Directory -Path $targetRoot -Force | Out-Null
  New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
}

foreach ($name in $skillNames) {
  $source = Join-Path $sourceRoot $name
  $target = Join-Path $targetRoot $name
  $backup = Join-Path $backupRoot $name

  if (Test-Path -LiteralPath $target) {
    Write-Output "Backing up existing $name -> $backup"
    if (-not $DryRun) {
      Move-Item -LiteralPath $target -Destination $backup
    }
  }

  Write-Output "Mirroring $name"
  if (-not $DryRun) {
    Copy-Item -LiteralPath $source -Destination $target -Recurse
  }
}

Write-Output "Mirrored $($skillNames.Count) skills."
if (-not $DryRun) {
  Write-Output "Backup root: $backupRoot"
  Write-Output "Restart Codex to pick up new skills."
}
