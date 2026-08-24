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

$ProjectPythonW = Join-Path $VenvRoot "Scripts\pythonw.exe"
if (-not (Test-Path -LiteralPath $ProjectPythonW)) {
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Start-Process `
    -WindowStyle Hidden `
    -FilePath $ProjectPythonW `
    -ArgumentList "-m", "tg_video_downloader", "gui" `
    -WorkingDirectory $ProjectRoot
