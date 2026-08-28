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
# 通过 package script 解析本地 node_modules/.bin；部分 Windows 环境下
# `pnpm exec vite` 无法找到同一个已安装的 Vite 可执行文件。
& $Pnpm run dev --port $Port
if ($LASTEXITCODE -ne 0) {
    throw "Frontend dev server exited with code $LASTEXITCODE"
}
