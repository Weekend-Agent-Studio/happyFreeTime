param(
    [int]$Port = 5173
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$FrontendRoot = Join-Path $ProjectRoot "frontend"
$KnownNpm = "D:\nodejs\npm.cmd"
$NpmCommand = Get-Command npm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1

if ($NpmCommand) {
    $Npm = $NpmCommand.Source
} elseif (Test-Path -LiteralPath $KnownNpm) {
    $Npm = $KnownNpm
} else {
    throw "npm was not found. Install Node.js or update KnownNpm in this script."
}

if (-not (Test-Path -LiteralPath (Join-Path $FrontendRoot "node_modules"))) {
    & $Npm --prefix $FrontendRoot install --no-package-lock
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend dependency installation exited with code $LASTEXITCODE"
    }
}

# --prefix lets npm resolve the frontend package without changing the caller's
# current directory. The extra -- forwards Vite options through npm run.
& $Npm --prefix $FrontendRoot run dev -- --port $Port --strictPort
if ($LASTEXITCODE -ne 0) {
    throw "Frontend dev server exited with code $LASTEXITCODE"
}
