$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TempRoot = Join-Path $ProjectRoot ".tmp"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$VenvRoot = Join-Path $ProjectRoot ".venv"

[System.IO.Directory]::CreateDirectory($TempRoot) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $CacheRoot "pip")) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $CacheRoot "pycache")) | Out-Null

$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:PYTHONPYCACHEPREFIX = Join-Path $CacheRoot "pycache"

if (-not (Test-Path -LiteralPath (Join-Path $VenvRoot "Scripts\python.exe"))) {
    $PythonLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($null -ne $PythonLauncher) {
        & py -3 -m venv $VenvRoot
    } else {
        & python -m venv $VenvRoot
    }
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$ProjectPython = Join-Path $VenvRoot "Scripts\python.exe"
& $ProjectPython -m pip install --disable-pip-version-check -e "${ProjectRoot}[dev]"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
& $ProjectPython -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11 or newer is required'"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
