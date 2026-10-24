<#
.SYNOPSIS
Start backend and frontend together. PublicDemo is the default local preview.
.EXAMPLE
.\scripts\start_local.ps1
.EXAMPLE
.\scripts\start_local.ps1 -Mode Development
.EXAMPLE
.\scripts\start_local.ps1 -Mode Development -FullLlm
#>
param(
    [ValidateSet("PublicDemo", "Development")]
    [string]$Mode = "PublicDemo",
    [int]$ApiPort = 0,
    [int]$FrontendPort = 0,
    [switch]$FullLlm,
    [switch]$Check
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "Missing .venv Python. Install backend dependencies first." }
$LaunchArgs = @((Join-Path $PSScriptRoot "start_local.py"), "--mode", $Mode, "--api-port", $ApiPort, "--frontend-port", $FrontendPort)
if ($FullLlm) { $LaunchArgs += "--full-llm" }
if ($Check) { $LaunchArgs += "--check" }
& $Python @LaunchArgs
exit $LASTEXITCODE
