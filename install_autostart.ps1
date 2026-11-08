# install_autostart.ps1 - register a Windows Scheduled Task that starts the
# Wemo web app at boot. Run once, from an ADMINISTRATOR PowerShell:
#     powershell -ExecutionPolicy Bypass -File .\install_autostart.ps1
# Remove later with:  Unregister-ScheduledTask -TaskName "Wemo Controller" -Confirm:$false

$ErrorActionPreference = "Stop"
$taskName = "Wemo Controller"
$script   = Join-Path $PSScriptRoot "run_tailscale.ps1"

# WHAT to run: PowerShell executing our launcher, hidden, ignoring the
# machine's script-execution policy for this one script.
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`"" `
    -WorkingDirectory $PSScriptRoot

# WHEN: at Windows startup (not at login), plus a 30 s delay so Windows and
# Tailscale's service can finish starting. The launcher also waits for the
# Tailscale address, so this is belt and braces.
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT30S"

# WHO: your own account, in "S4U" mode = "run whether the user is logged on or
# not", WITHOUT storing your password. It runs with your normal (non-admin)
# rights, not SYSTEM, so a bug in the app can't touch more than you could.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
    -LogonType S4U -RunLevel Limited

# HOW: a laptop-friendly policy. Keep running on battery, never time out, and
# if the app crashes or exits, restart it every minute (up to 999 times).
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description "Starts the Wemo plug web app on the Tailscale address at boot." -Force

Write-Host "Registered '$taskName'. Start it now with: Start-ScheduledTask -TaskName '$taskName'"
