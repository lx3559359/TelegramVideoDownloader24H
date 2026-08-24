$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TempRoot = Join-Path $ProjectRoot ".tmp"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$VenvRoot = Join-Path $ProjectRoot ".venv"

$LocalDirectories = @(
    (Join-Path $ProjectRoot ".runtime"),
    $CacheRoot,
    $TempRoot,
    (Join-Path $ProjectRoot "logs"),
    (Join-Path $ProjectRoot "downloads")
)
foreach ($Directory in $LocalDirectories) {
    [System.IO.Directory]::CreateDirectory($Directory) | Out-Null
}
[System.IO.Directory]::CreateDirectory((Join-Path $CacheRoot "pip")) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $CacheRoot "pycache")) | Out-Null

$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:PYTHONPYCACHEPREFIX = Join-Path $CacheRoot "pycache"

$ProjectPython = Join-Path $VenvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $ProjectPython)) {
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
    if ($LASTEXITCODE -ne 0) {
        exit 1
    }
}

& $ProjectPython -m pytest -q
if ($LASTEXITCODE -ne 0) {
    exit 1
}
& $ProjectPython -m compileall -q (Join-Path $ProjectRoot "src")
if ($LASTEXITCODE -ne 0) {
    exit 1
}

$RootPrefix = $ProjectRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
foreach ($Directory in $LocalDirectories) {
    $ResolvedDirectory = (Resolve-Path -LiteralPath $Directory).Path
    if (-not $ResolvedDirectory.StartsWith($RootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        exit 1
    }
}
exit 0
