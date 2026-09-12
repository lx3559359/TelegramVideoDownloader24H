param([Parameter(Mandatory = $true)][string]$RequestPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$updateLock = $null
$installerHandle = $null
$runtime = $null
$root = $null
$request = $null
$ready = $false
$success = $false
$message = ''

function Safe-Path([string]$Value) {
    if ($Value -notmatch '^[A-Za-z]:[\\/]' -or $Value -match '["\r\n]' -or $Value.Substring(2).Contains(':')) {
        throw 'Invalid absolute path.'
    }
    $full = [IO.Path]::GetFullPath($Value).TrimEnd('\', '/')
    $item = $full
    while ($item) {
        if (Test-Path -LiteralPath $item) {
            if ((Get-Item -LiteralPath $item -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'Reparse points are not permitted in update paths.'
            }
        }
        $item = [IO.Path]::GetDirectoryName($item)
    }
    return $full
}

function Parent-Alive {
    return $null -ne (Get-Process -Id ([int]$request.parent_pid) -ErrorAction SilentlyContinue)
}

function App-Busy {
    $mutex = $null
    try {
        $mutex = [Threading.Mutex]::OpenExisting('TelegramVideoDownloader.Running')
        return $true
    } catch [Threading.WaitHandleCannotBeOpenedException] {
        # Existence, rather than mutex ownership, is the application guard.
    } finally {
        if ($mutex) { $mutex.Dispose() }
    }
    foreach ($name in @('gui.lock', 'downloader.lock', 'supervisor.pid')) {
        $path = Join-Path $runtime $name
        if (-not (Test-Path -LiteralPath $path)) { continue }
        $stream = $null
        try {
            $stream = [IO.File]::Open($path, 'Open', 'ReadWrite', 'None')
            $stream.Lock(0, 1)
            $stream.Unlock(0, 1)
        } catch [IO.IOException] { return $true }
        finally { if ($stream) { $stream.Dispose() } }
    }
    return $false
}

function Check-Installer {
    $installerHandle.Position = 0
    if ($installerHandle.Length -ne [long]$request.size) { throw 'Installer size mismatch.' }
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $hash = [BitConverter]::ToString($sha.ComputeHash($installerHandle)).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
    if ($hash -cne $request.sha256) { throw 'Installer hash mismatch.' }
}

function Save-Result {
    if (-not $runtime -or -not $updateLock) { return }
    $status = if ($success) { 'success' } else { 'failed' }
    $result = @{status=$status; message=$message; version=[string]$request.version} | ConvertTo-Json -Compress
    $resultFile = Safe-Path (Join-Path $runtime 'installer-update-result.json')
    [IO.File]::WriteAllText($resultFile, $result, (New-Object Text.UTF8Encoding($false)))
}

try {
    $requestFile = Safe-Path $RequestPath
    $stage = Safe-Path $PSScriptRoot
    if ($requestFile -ine (Join-Path $stage 'request.json')) { throw 'Request must be beside helper.' }
    $request = Get-Content -LiteralPath $requestFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $root = Safe-Path ([string]$request.root)
    if ($root.Length -le 3) { throw 'Installation root cannot be a drive root.' }
    $cache = Join-Path $root '.cache\installer-updates'
    if ([IO.Path]::GetDirectoryName($stage) -ine $cache -or
        [IO.Path]::GetFileName($stage) -notmatch '^[0-9a-fA-F]{32}$|^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$') {
        throw 'Helper is outside the installation update cache.'
    }
    $runtime = Safe-Path (Join-Path $root '.runtime')
    [IO.Directory]::CreateDirectory($runtime) | Out-Null
    if ($request.version -notmatch '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' -or $request.token -cnotmatch '^[0-9a-f]{32}$' -or
        $request.sha256 -cnotmatch '^[0-9a-f]{64}$' -or $request.restore_service -isnot [bool] -or
        ($request.size -isnot [int] -and $request.size -isnot [long]) -or $request.size -le 0 -or $request.size -gt 536870912 -or
        ($request.parent_pid -isnot [int] -and $request.parent_pid -isnot [long]) -or
        $request.parent_pid -le 0 -or $request.parent_pid -gt 2147483647 -or $request.parent_pid -eq $PID) {
        throw 'Invalid update request fields.'
    }
    $installer = Safe-Path ([string]$request.installer)
    $expected = Join-Path $stage ('TelegramVideoDownloader-v' + $request.version + '-Windows-x64-Setup.exe')
    if ($installer -ine $expected) { throw 'Installer must have the expected name inside the update cache.' }
    $updateLock = [IO.File]::Open((Safe-Path (Join-Path $runtime 'installer-update.lock')), 'OpenOrCreate', 'ReadWrite', 'None')
    $installerHandle = [IO.File]::Open($installer, 'Open', 'Read', 'Read')
    Check-Installer
    $cancel = Join-Path $stage 'request.cancel'
    if (Test-Path -LiteralPath $cancel) { throw 'Update cancelled.' }
    [IO.File]::WriteAllText((Join-Path $stage 'request.ready'), [string]$request.token)
    $ready = $true
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while ((Parent-Alive) -or (App-Busy)) {
        if (Test-Path -LiteralPath $cancel) { throw 'Update cancelled.' }
        if ([DateTime]::UtcNow -ge $deadline) { throw 'Application did not stop within 60 seconds; installation was not started.' }
        Start-Sleep -Milliseconds 200
    }
    if (Test-Path -LiteralPath $cancel) { throw 'Update cancelled.' }
    Check-Installer
    $logs = Safe-Path (Join-Path $root 'logs')
    [IO.Directory]::CreateDirectory($logs) | Out-Null
    $logFile = Safe-Path (Join-Path $logs 'installer-update.log')
    $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /DIR="' + $root + '" /LOG="' + $logFile + '"'
    $process = Start-Process -FilePath $installer -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne 0) { throw ('Installer failed with exit code ' + $process.ExitCode + '. Files may be partially updated; no rollback was performed.') }
    $success = $true
    $message = 'Update installed successfully.'
} catch {
    $message = $_.Exception.Message
} finally {
    if ($installerHandle) { $installerHandle.Dispose() }
    if ($updateLock) { $updateLock.Dispose() }
}

$restart = $ready -and -not (Parent-Alive)
if ($restart -and $success -and $request.restore_service) {
    try {
        $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
        $supervisor = Safe-Path (Join-Path $root 'scripts\run-supervisor.ps1')
        if (-not (Test-Path -LiteralPath $supervisor -PathType Leaf)) { throw 'Supervisor script is missing.' }
        $stop = Safe-Path (Join-Path $runtime 'stop.flag')
        if (Test-Path -LiteralPath $stop) { Remove-Item -LiteralPath $stop -Force }
        $backend = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -WindowStyle Hidden -WorkingDirectory $root -ArgumentList ('-NoProfile -ExecutionPolicy Bypass -File "' + $supervisor + '"') -PassThru
        $deadline = [DateTime]::UtcNow.AddSeconds(10)
        $backendReady = $false
        while ([DateTime]::UtcNow -lt $deadline -and -not $backend.HasExited) {
            $pidFile = Safe-Path (Join-Path $runtime 'supervisor.pid')
            if (Test-Path -LiteralPath $pidFile) {
                try { $probe = [IO.File]::Open($pidFile, 'Open', 'ReadWrite', 'None'); $probe.Dispose() }
                catch [IO.IOException] { $backendReady = $true; break }
            }
            Start-Sleep -Milliseconds 200
        }
        if (-not $backendReady) { throw 'Supervisor did not become ready within 10 seconds.' }
    } catch {
        $success = $false
        $message += ' Background restart failed; start the service from the app: ' + $_.Exception.Message
    }
}
try { Save-Result } catch { Write-Warning $_.Exception.Message }
if ($restart) {
    try {
        $exe = Safe-Path (Join-Path $root 'TelegramVideoDownloader.exe')
        if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { throw 'Application executable is missing.' }
        $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
        Start-Process -FilePath $exe -WorkingDirectory $root | Out-Null
    } catch {
        $success = $false
        $message += ' GUI restart failed; open TelegramVideoDownloader.exe manually: ' + $_.Exception.Message
        try { Save-Result } catch { Write-Warning $_.Exception.Message }
    }
}
if (-not $success) { Write-Error $message -ErrorAction Continue; exit 1 }
exit 0
