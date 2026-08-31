param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectRoot,
    [int]$IntervalSeconds = 300
)

$ErrorActionPreference = 'Continue'
$resolvedRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$statusRoot = Join-Path $resolvedRoot 'docs\status'
$snapshotRoot = Join-Path $statusRoot 'snapshots'
$logRoot = Join-Path $statusRoot 'logs'
$heartbeatPath = Join-Path $statusRoot 'WATCHDOG_HEARTBEAT.json'
$stopPath = Join-Path $statusRoot '.watchdog.stop'
$pidPath = Join-Path $statusRoot '.watchdog.pid'

New-Item -ItemType Directory -Force -Path $snapshotRoot, $logRoot | Out-Null
if (Test-Path -LiteralPath $stopPath) {
    Remove-Item -LiteralPath $stopPath -Force
}
Set-Content -LiteralPath $pidPath -Encoding ascii -Value $PID

try {
    while (-not (Test-Path -LiteralPath $stopPath)) {
        $started = Get-Date
        $stamp = $started.ToString('yyyy-MM-dd_HHmmss')
        $snapshotPath = Join-Path $snapshotRoot ($stamp + '.md')
        $verifyLog = Join-Path $logRoot ($stamp + '_verify_datasheet.log')

        $files = Get-ChildItem -LiteralPath $resolvedRoot -File -Recurse -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notlike (Join-Path $resolvedRoot '.git\*') -and $_.FullName -notlike ($statusRoot + '\*') } |
            Sort-Object FullName
        $latest = $files | Sort-Object LastWriteTime -Descending | Select-Object -First 20

        $gitOutput = @(& git -c "safe.directory=$($resolvedRoot.Replace('\','/'))" -C $resolvedRoot status --short --untracked-files=all 2>&1)
        $gitExit = $LASTEXITCODE

        $verifyScript = Join-Path $resolvedRoot 'docs\datasheet\_internal\verify_datasheet.py'
        if (Test-Path -LiteralPath $verifyScript) {
            $verifyOutput = @(& python $verifyScript 2>&1)
            $verifyExit = $LASTEXITCODE
            $verifyOutput | Set-Content -LiteralPath $verifyLog -Encoding utf8
        }
        else {
            $verifyExit = -1
            'verify script not found' | Set-Content -LiteralPath $verifyLog -Encoding utf8
        }

        $lines = @(
            '# Watchdog Snapshot',
            '',
            ('- Started: {0}' -f $started.ToString('yyyy-MM-dd HH:mm:ss zzz')),
            "- File count: $($files.Count)",
            "- Git exit code: $gitExit",
            "- Datasheet verification exit code: $verifyExit",
            "- Verification log: ../logs/$($stamp)_verify_datasheet.log",
            '',
            '## Git Changes',
            '',
            '```text'
        )
        $lines += if ($gitOutput.Count -gt 0) { $gitOutput } else { '(no changes)' }
        $lines += @('```', '', '## Recently Modified Files', '')
        $lines += $latest | ForEach-Object {
            $relative = $_.FullName.Substring($resolvedRoot.Length + 1).Replace('\', '/')
            ('- {0} - {1}' -f $relative, $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'))
        }
        $lines | Set-Content -LiteralPath $snapshotPath -Encoding utf8

        [ordered]@{
            status = 'running'
            pid = $PID
            last_check_started = $started.ToString('o')
            last_check_finished = (Get-Date).ToString('o')
            interval_seconds = $IntervalSeconds
            snapshot = $snapshotPath.Substring($resolvedRoot.Length + 1).Replace('\', '/')
            git_exit_code = $gitExit
            verify_datasheet_exit_code = $verifyExit
        } | ConvertTo-Json | Set-Content -LiteralPath $heartbeatPath -Encoding utf8

        $remaining = $IntervalSeconds
        while ($remaining -gt 0 -and -not (Test-Path -LiteralPath $stopPath)) {
            $slice = [Math]::Min(5, $remaining)
            Start-Sleep -Seconds $slice
            $remaining -= $slice
        }
    }
}
finally {
    [ordered]@{
        status = 'stopped'
        pid = $PID
        stopped_at = (Get-Date).ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath $heartbeatPath -Encoding utf8
    Remove-Item -LiteralPath $pidPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $stopPath -Force -ErrorAction SilentlyContinue
}
