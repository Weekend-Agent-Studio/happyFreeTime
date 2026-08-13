param(
    [string]$Python = "D:\NWPU_career\anaconda3\envs\PyTorch\python.exe",
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python environment not found: $Python"
}

Set-Location -LiteralPath $ProjectRoot
$env:HFT_DEMO_MODE = "1"
$env:LANGGRAPH_STRICT_MSGPACK = "true"

& $Python -m uvicorn app.api.server:app --host 127.0.0.1 --port $Port
