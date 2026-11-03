# Hackathon Watch - desktop launcher (Windows)
# Creates .venv if missing (uv preferred, fallback py -3.12 / python),
# ensures pywebview is installed, then runs desktop.py.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Web = "$($Root)\desktop.py"

if (-not (Test-Path $Py)) {
    Write-Host "[hw] creating virtualenv..."
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        uv venv --python 3.12 $Venv | Out-Null
    } else {
        $py312 = Get-Command py -ErrorAction SilentlyContinue
        if ($py312) { py -3.12 -m venv $Venv } else { python -m venv $Venv }
    }
    if (-not (Test-Path $Py)) { throw "Failed to create venv at $Venv" }
}

$hasWebview = & $Py -c "import webview" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[hw] installing pywebview..."
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($uv) {
        uv pip install --python $Py pywebview
    } else {
        & $Py -m pip install --quiet pywebview
    }
    if ($LASTEXITCODE -ne 0) { throw "pywebview install failed" }
}

if (-not (Test-Path $Web)) { throw "missing $Web" }
Write-Host "[hw] launching $Web"
& $Py $Web
exit $LASTEXITCODE
