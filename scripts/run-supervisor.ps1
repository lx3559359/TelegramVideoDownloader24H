$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TempRoot = Join-Path $ProjectRoot ".tmp"
$CacheRoot = Join-Path $ProjectRoot ".cache"
$RuntimeRoot = Join-Path $ProjectRoot ".runtime"
$VenvRoot = Join-Path $ProjectRoot ".venv"

[System.IO.Directory]::CreateDirectory($TempRoot) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $CacheRoot "pip")) | Out-Null
[System.IO.Directory]::CreateDirectory((Join-Path $CacheRoot "pycache")) | Out-Null
[System.IO.Directory]::CreateDirectory($RuntimeRoot) | Out-Null

$env:TEMP = $TempRoot
$env:TMP = $TempRoot
$env:PIP_CACHE_DIR = Join-Path $CacheRoot "pip"
$env:PYTHONPYCACHEPREFIX = Join-Path $CacheRoot "pycache"

$ProjectPython = Join-Path $VenvRoot "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $ProjectPython)) {
    & (Join-Path $PSScriptRoot "bootstrap.ps1")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

$StopFlag = Join-Path $RuntimeRoot "stop.flag"
$SupervisorPid = Join-Path $RuntimeRoot "supervisor.pid"
$PidStream = $null
try {
    $PidStream = [System.IO.File]::Open(
        $SupervisorPid,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
} catch [System.IO.IOException] {
    exit 0
}

try {
    $PidBytes = [System.Text.Encoding]::ASCII.GetBytes([string]$PID)
    $PidStream.SetLength(0)
    $PidStream.Write($PidBytes, 0, $PidBytes.Length)
    $PidStream.Flush($true)

    $DelaySeconds = 5
    while (-not (Test-Path -LiteralPath $StopFlag)) {
        $StartedAt = Get-Date
        $Process = Start-Process `
            -WindowStyle Hidden `
            -FilePath $ProjectPython `
            -ArgumentList "-m", "tg_video_downloader", "service" `
            -WorkingDirectory $ProjectRoot `
            -Wait `
            -PassThru
        $RunSeconds = ((Get-Date) - $StartedAt).TotalSeconds

        if (Test-Path -LiteralPath $StopFlag) {
            break
        }
        if ($RunSeconds -ge 600) {
            $DelaySeconds = 5
        }
        Start-Sleep -Seconds $DelaySeconds
        $DelaySeconds = [Math]::Min($DelaySeconds * 2, 300)
    }
} finally {
    if ($null -ne $PidStream) {
        $PidStream.Dispose()
    }
    if (Test-Path -LiteralPath $SupervisorPid) {
        $PidText = (Get-Content -LiteralPath $SupervisorPid -Raw).Trim()
        if ($PidText -eq [string]$PID) {
            Remove-Item -LiteralPath $SupervisorPid -Force
        }
    }
}
