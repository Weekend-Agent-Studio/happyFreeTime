param(
    [int]$Port = 5173
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$BundledPnpm = "C:\Users\UpAndUp\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"
$PnpmCommand = Get-Command pnpm -ErrorAction SilentlyContinue

if ($PnpmCommand) {
    $Pnpm = $PnpmCommand.Source
} elseif (Test-Path -LiteralPath $BundledPnpm) {
    $Pnpm = $BundledPnpm
} else {
    throw "pnpm was not found. Install pnpm or update BundledPnpm in this script."
}

Set-Location -LiteralPath $FrontendRoot
if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "node_modules"))) {
    & $Pnpm install --store-dir (Join-Path $ProjectRoot ".pnpm-store")
}
& $Pnpm exec vite --host 127.0.0.1 --port $Port
