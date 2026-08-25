param(
    [Parameter(Mandatory = $true)][string]$ProjectRoot,
    [Parameter(Mandatory = $true)][string]$RequestPath,
    [switch]$NoRelaunch,
    [switch]$NoServiceRestart,
    [switch]$SkipImportSmoke
)

$ErrorActionPreference = "Stop"
$ResultStatus = "failed"
$ResultMessage = "Update failed before completion."
$Request = $null
$RequestValidated = $false
$Merged = $false

function Invoke-Checked([scriptblock]$Action, [string]$Stage) {
    $output = & $Action
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
        throw [System.InvalidOperationException]::new(
            ("{0} failed with exit code {1}" -f $Stage, $exitCode)
        )
    }
    return $output
}

function Write-UpdateLog([string]$Message) {
    $line = "{0} {1}" -f ([DateTimeOffset]::UtcNow.ToString('o')), $Message
    Add-Content -LiteralPath (Join-Path $ProjectRoot 'logs\update.log') -Value $line -Encoding UTF8
}

function Test-FileLocked([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $stream = $null
    try {
        $stream = [System.IO.File]::Open(
            $Path,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::ReadWrite,
            [System.IO.FileShare]::None
        )
        return $false
    } catch [System.IO.IOException] {
        return $true
    } finally {
        if ($null -ne $stream) {
            $stream.Dispose()
        }
    }
}

function Write-UpdateResult(
    [string]$Token,
    [string]$Tag,
    [string]$Status,
    [string]$Message
) {
    $runtime = Join-Path $ProjectRoot '.runtime'
    [System.IO.Directory]::CreateDirectory($runtime) | Out-Null
    $destination = Join-Path $runtime 'update-result.json'
    $temporary = Join-Path $runtime ("update-result.{0}.tmp" -f $Token)
    $backup = Join-Path $runtime ("update-result.{0}.bak" -f $Token)
    $result = [ordered]@{
        token = $Token
        tag = $Tag
        status = $Status
        message = $Message
        completed_at = [DateTimeOffset]::UtcNow.ToString('o')
    }
    $json = ($result | ConvertTo-Json -Compress) + "`n"
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($temporary, $json, $utf8)
    if (Test-Path -LiteralPath $destination) {
        try {
            [System.IO.File]::Replace($temporary, $destination, $backup)
        } finally {
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        }
    } else {
        [System.IO.File]::Move($temporary, $destination)
    }
}

try {
    $ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $RequestPath = (Resolve-Path -LiteralPath $RequestPath).Path
    if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.git'))) {
        throw [System.InvalidOperationException]::new('Project root is not a Git worktree.')
    }
    $expectedRequest = [System.IO.Path]::GetFullPath(
        (Join-Path $ProjectRoot '.runtime\update-request.json')
    )
    if (-not [string]::Equals(
        $RequestPath,
        $expectedRequest,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw [System.InvalidOperationException]::new('Unexpected update request path.')
    }

    [System.IO.Directory]::CreateDirectory((Join-Path $ProjectRoot 'logs')) | Out-Null
    Write-UpdateLog 'stage=request-validation'
    $Request = Get-Content -LiteralPath $RequestPath -Raw | ConvertFrom-Json
    $expectedNames = @(
        'base_commit',
        'restore_service',
        'tag',
        'target_commit',
        'token'
    ) | Sort-Object
    $actualNames = @($Request.PSObject.Properties.Name | Sort-Object)
    if (@(Compare-Object -ReferenceObject $expectedNames -DifferenceObject $actualNames).Count -ne 0) {
        throw [System.InvalidOperationException]::new('Update request fields are invalid.')
    }
    if ($Request.token -notmatch '^[0-9a-f]{32}$') {
        throw [System.InvalidOperationException]::new('Update request token is invalid.')
    }
    if ($Request.tag -notmatch '^v\d+\.\d+\.\d+$') {
        throw [System.InvalidOperationException]::new('Update request tag is invalid.')
    }
    if (
        $Request.base_commit -notmatch '^[0-9a-f]{40}$' -or
        $Request.target_commit -notmatch '^[0-9a-f]{40}$'
    ) {
        throw [System.InvalidOperationException]::new('Update request commit is invalid.')
    }
    if ($Request.restore_service -isnot [bool]) {
        throw [System.InvalidOperationException]::new('Service restore state is invalid.')
    }
    $RequestValidated = $true

    $guiLock = Join-Path $ProjectRoot '.runtime\gui.lock'
    $guiDeadline = [DateTimeOffset]::UtcNow.AddSeconds(30)
    while (Test-FileLocked $guiLock) {
        if ([DateTimeOffset]::UtcNow -ge $guiDeadline) {
            throw [System.TimeoutException]::new('GUI lock did not release within 30 seconds.')
        }
        Start-Sleep -Milliseconds 100
    }

    Write-UpdateLog ("stage=preflight tag={0} base={1} target={2}" -f $Request.tag, $Request.base_commit, $Request.target_commit)
    $branch = (@(Invoke-Checked { & git -C $ProjectRoot branch --show-current } 'read-branch') -join "`n").Trim()
    if ($branch -ne 'master') {
        throw [System.InvalidOperationException]::new('Online update requires master branch.')
    }
    $head = (@(Invoke-Checked { & git -C $ProjectRoot rev-parse HEAD } 'read-head') -join "`n").Trim()
    if ($head -ne $Request.base_commit) {
        throw [System.InvalidOperationException]::new('HEAD changed after update check.')
    }
    $porcelain = (@(Invoke-Checked { & git -C $ProjectRoot status --porcelain --untracked-files=all } 'read-status') -join "`n").Trim()
    if ($porcelain) {
        throw [System.InvalidOperationException]::new('Git worktree is not clean.')
    }
    $releaseRef = "refs/tg-video-downloader/releases/{0}" -f $Request.tag
    $tagTarget = (@(Invoke-Checked { & git -C $ProjectRoot rev-list -n 1 $releaseRef } 'read-tag') -join "`n").Trim()
    if ($tagTarget -ne $Request.target_commit) {
        throw [System.InvalidOperationException]::new('Stable tag target changed.')
    }

    Write-UpdateLog 'stage=fast-forward'
    Invoke-Checked { & git -C $ProjectRoot merge --ff-only $Request.target_commit } 'fast-forward'
    $Merged = $true
    try {
        Write-UpdateLog 'stage=bootstrap'
        $bootstrap = Join-Path $ProjectRoot 'scripts\bootstrap.ps1'
        Invoke-Checked { & $bootstrap } 'bootstrap'
        if (-not $SkipImportSmoke) {
            Write-UpdateLog 'stage=import-smoke'
            $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
            Invoke-Checked { & $python -c "import cryptg, pystray, PIL, tg_video_downloader" } 'import-smoke'
        }
        $ResultStatus = 'success'
        $ResultMessage = 'Update installed successfully.'
        Write-UpdateLog 'stage=success'
    } catch {
        Write-UpdateLog ("stage=post-merge-failure type={0}" -f $_.Exception.GetType().Name)
        $rollbackHead = (@(Invoke-Checked { & git -C $ProjectRoot rev-parse HEAD } 'rollback-head') -join "`n").Trim()
        $rollbackStatus = (@(Invoke-Checked { & git -C $ProjectRoot status --porcelain --untracked-files=all } 'rollback-status') -join "`n").Trim()
        if ($rollbackHead -eq $Request.target_commit -and -not $rollbackStatus) {
            try {
                Write-UpdateLog 'stage=rollback'
                Invoke-Checked { & git -C $ProjectRoot update-ref refs/heads/master $Request.base_commit $Request.target_commit } 'rollback-ref'
                Invoke-Checked { & git -C $ProjectRoot restore --source=HEAD --staged --worktree -- . } 'rollback-files'
                $rollbackBootstrap = Join-Path $ProjectRoot 'scripts\bootstrap.ps1'
                Invoke-Checked { & $rollbackBootstrap } 'rollback-bootstrap'
                $ResultStatus = 'rolled_back'
                $ResultMessage = 'Update failed and the previous version was restored.'
                Write-UpdateLog 'stage=rolled-back'
            } catch {
                $ResultStatus = 'failed'
                $ResultMessage = 'Update and safe rollback both failed; manual recovery is required.'
                Write-UpdateLog ("stage=rollback-failure type={0}" -f $_.Exception.GetType().Name)
            }
        } else {
            $ResultStatus = 'failed'
            $ResultMessage = 'Update failed after the worktree changed; automatic rollback was refused.'
            Write-UpdateLog 'stage=rollback-refused'
        }
    }
} catch {
    $ResultStatus = 'failed'
    $ResultMessage = 'Update safety validation failed; no automatic version change was made.'
    if ($RequestValidated) {
        Write-UpdateLog ("stage=pre-merge-failure type={0} merged={1}" -f $_.Exception.GetType().Name, $Merged)
    }
} finally {
    if ($RequestValidated) {
        try {
            Write-UpdateResult $Request.token $Request.tag $ResultStatus $ResultMessage
            Remove-Item -LiteralPath $RequestPath -Force -ErrorAction SilentlyContinue
        } catch {
            Write-UpdateLog ("stage=result-write-failure type={0}" -f $_.Exception.GetType().Name)
            $ResultStatus = 'failed'
        }

        if ([bool]$Request.restore_service -and -not $NoServiceRestart) {
            Remove-Item -LiteralPath (Join-Path $ProjectRoot '.runtime\stop.flag') -Force -ErrorAction SilentlyContinue
            $supervisorScript = Join-Path $ProjectRoot 'scripts\run-supervisor.ps1'
            $supervisorArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $supervisorScript
            Start-Process `
                -WindowStyle Hidden `
                -FilePath 'powershell.exe' `
                -ArgumentList $supervisorArguments `
                -WorkingDirectory $ProjectRoot
        }
        if (-not $NoRelaunch) {
            $guiScript = Join-Path $ProjectRoot 'scripts\launch-gui.ps1'
            $guiArguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}"' -f $guiScript
            Start-Process `
                -WindowStyle Hidden `
                -FilePath 'powershell.exe' `
                -ArgumentList $guiArguments `
                -WorkingDirectory $ProjectRoot
        }
    }
}

if ($ResultStatus -eq 'success') {
    exit 0
}
exit 1
