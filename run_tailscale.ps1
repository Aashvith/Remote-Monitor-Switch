# run_tailscale.ps1 - start the web app listening ONLY on this laptop's Tailscale address.
#
# Binding to the 100.x.x.x address (instead of 0.0.0.0, "every interface")
# means the app is invisible on your home Wi-Fi/LAN: only devices signed in to
# your tailnet can connect. The address is looked up with `tailscale ip -4`.
#
# At Windows boot, Tailscale may not have its address yet when this script
# starts, so we wait (forever, logging once a minute) instead of failing.
# Logs go to the logs\ folder (ignored by git) because a task running at boot
# has no visible window: wemo-app.log = this launcher, app.out.log/app.err.log = Flask.

$ErrorActionPreference = "Stop"
$projectDir = $PSScriptRoot
$tailscale  = "C:\Program Files\Tailscale\tailscale.exe"
$logDir     = Join-Path $projectDir "logs"
$log        = Join-Path $logDir "wemo-app.log"
New-Item -ItemType Directory -Force $logDir | Out-Null

function Write-Log($msg) { "$(Get-Date -Format 's') $msg" | Add-Content -Path $log }

# Keep the log from growing forever: start fresh if it passes 1 MB.
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item $log "$log.old" -Force }

# Tailscale's address range is 100.64.0.0/10 (100.64.x.x - 100.127.x.x).
$pattern = '^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+$'
$ip = $null
$waitStart = Get-Date
$lastLogged = [datetime]::MinValue
while (-not $ip) {
    try {
        $candidate = (& $tailscale ip -4 2>$null | Select-Object -First 1)
        if ($candidate -and $candidate.Trim() -match $pattern) { $ip = $candidate.Trim() }
    } catch { }
    if (-not $ip) {
        # Never give up: log a "still waiting" line once a minute, pause, retry.
        if (((Get-Date) - $lastLogged).TotalSeconds -ge 60) {
            Write-Log ("Still waiting for a Tailscale address (100.64.0.0/10), {0} min so far..." -f [int]((Get-Date) - $waitStart).TotalMinutes)
            $lastLogged = Get-Date
        }
        Start-Sleep -Seconds 5
    }
}

# Read local settings (WEMO_HOST etc.) from the git-ignored config file.
$config = Join-Path $projectDir "config.local.env"
if (-not (Test-Path $config)) {
    Write-Log "Missing config.local.env - copy config.example.env and set WEMO_HOST."
    exit 1
}
foreach ($line in Get-Content $config) {
    if ($line -match '^\s*([^#=\s][^=]*)=(.*)$') {
        Set-Item -Path "env:$($Matches[1].Trim())" -Value $Matches[2].Trim()
    }
}

# Clean up any stale copy still holding port 5000 (e.g. one started by hand).
# Only python.exe processes are touched; the venv launcher parent is stopped too.
$stale = Get-NetTCPConnection -LocalPort 5000 -State Listen -ErrorAction SilentlyContinue
foreach ($c in $stale) {
    $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)" -ErrorAction SilentlyContinue
    if ($p -and $p.Name -eq 'python.exe') {
        $parent = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
        Write-Log "Stopping stale app copy on port 5000 (PID $($p.ProcessId))"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        if ($parent -and $parent.Name -eq 'python.exe') { Stop-Process -Id $parent.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    } elseif ($p) {
        Write-Log "Port 5000 is held by $($p.Name) (PID $($p.ProcessId)), not python; leaving it alone."
    }
}

$env:BIND_HOST = $ip
Write-Log "Starting Wemo Controller on http://${ip}:5000 (Tailscale only)"
Write-Host "Wemo Controller listening on http://${ip}:5000 (Tailscale only)"

# Run the app with Start-Process, sending Python's stdout and stderr straight
# to their own files. (Piping a program's stderr through PowerShell 5.1 turns
# every line - including Flask's harmless startup messages - into an error,
# which together with $ErrorActionPreference = "Stop" killed the app.)
$proc = Start-Process -FilePath "$projectDir\.venv\Scripts\python.exe" `
    -ArgumentList "`"$projectDir\app.py`"" -WorkingDirectory $projectDir `
    -RedirectStandardOutput (Join-Path $logDir "app.out.log") `
    -RedirectStandardError  (Join-Path $logDir "app.err.log") `
    -NoNewWindow -Wait -PassThru
Write-Log "App exited with code $($proc.ExitCode)"
exit $proc.ExitCode
