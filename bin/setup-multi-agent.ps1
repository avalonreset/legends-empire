# Legends Obsidian: PowerShell 5.1+ / 7 entry point. No profile changes.
[CmdletBinding()]
param(
    [ValidateSet('dry-run', 'check', 'apply')][string]$Mode = 'dry-run',
    [string[]]$Hosts = @(),
    [string]$Workspace,
    [string]$HomeDirectory,
    [ValidateSet('user', 'workspace')][string]$Scope = 'user'
)
$ErrorActionPreference = 'Stop'
$installer = Join-Path $PSScriptRoot 'setup_multi_agent.py'
$installerArgs = @('-B', $installer, "--$Mode", '--scope', $Scope)
# pwsh -File receives comma-separated host names as a single string, while
# direct script invocation can bind a string array. Support both spellings.
foreach ($hostGroup in $Hosts) {
    foreach ($targetHost in ($hostGroup -split ',')) {
        $installerArgs += @('--host', $targetHost.Trim())
    }
}
if ($Workspace) { $installerArgs += @('--workspace', $Workspace) }
if ($HomeDirectory) { $installerArgs += @('--home', $HomeDirectory) }
$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $pythonCommand) { $pythonCommand = Get-Command python3 -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 }
if (-not $pythonCommand) { throw 'Python 3.11+ is required.' }
& $pythonCommand.Source @installerArgs
exit $LASTEXITCODE
