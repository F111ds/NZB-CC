<#
  setup_altmount_startup.ps1 - start AltMount (Usenet streaming) by itself when you log in, hidden, like SABnzbd.

  Without this, AltMount only runs while its PowerShell window is open, and the phone's Stream buttons and
  "Play on TV" stop working when that window closes. This makes a task "AltMount (Usenet streaming)" that starts
  it at logon (no window), and restarts it if it stops.

  Run try_altmount.ps1 first (it installs AltMount). Then, in PowerShell:
    powershell -ExecutionPolicy Bypass -File .\setup_altmount_startup.ps1
    powershell -ExecutionPolicy Bypass -File .\setup_altmount_startup.ps1 -Undo     # remove the task again
#>
param(
    [string]$Folder = "F:\AltMount",
    [int]$Port = 8090,
    [switch]$Undo
)

$ErrorActionPreference = "Stop"
$TaskName = "AltMount (Usenet streaming)"
function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

if ($Undo) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like "altmount*" } | Stop-Process -Force -ErrorAction SilentlyContinue
        Say "Removed. AltMount no longer starts by itself (start it by hand with try_altmount.ps1)." Green
    } else {
        Say "There was no AltMount task." Yellow
    }
    exit 0
}

$exe = Get-ChildItem -Path $Folder -Filter "altmount*windows*.exe" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $exe) { Say "AltMount isn't in $Folder. Run try_altmount.ps1 first (or pass -Folder)." Red; exit 1 }
if (-not (Test-Path (Join-Path $Folder "config.yaml"))) { Say "AltMount hasn't been started yet. Run try_altmount.ps1 once first." Red; exit 1 }

# The login secret AltMount needs (the same one try_altmount.ps1 made, so you stay signed in).
$secretFile = Join-Path $Folder "jwt_secret.txt"
if (-not (Test-Path $secretFile)) {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [IO.File]::WriteAllText($secretFile, [Convert]::ToBase64String($bytes))
}

# A small launcher next to AltMount: sets what it needs, then runs it, its messages going to console.log.
$launcher = Join-Path $Folder "start_altmount.ps1"
$exeName = $exe.Name
@"
# Started by the task "$TaskName" (setup_altmount_startup.ps1). Runs AltMount hidden; messages go to console.log.
`$env:JWT_SECRET = (Get-Content (Join-Path `$PSScriptRoot 'jwt_secret.txt') -Raw).Trim()
`$env:PORT = '$Port'
Set-Location `$PSScriptRoot
& (Join-Path `$PSScriptRoot '$exeName') serve *>> (Join-Path `$PSScriptRoot 'console.log')
exit `$LASTEXITCODE
"@ | Set-Content -Path $launcher -Encoding ASCII

# Running already (e.g. in the PowerShell window from tonight)? Stop it, so the task can take over.
$running = Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.ProcessName -like "altmount*" }
if ($running) {
    Say "AltMount is running in a window. Stopping it so it can run hidden instead..." Cyan
    $running | Stop-Process -Force
    Start-Sleep -Seconds 2
}

$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$launcher`"" -WorkingDirectory $Folder
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
    -Description "Starts AltMount (Usenet streaming for the phone app and the TV) when $env:USERNAME logs in." -Force | Out-Null
Say "Task '$TaskName' created: starts at logon, hidden." Green

Start-ScheduledTask -TaskName $TaskName
$up = $false
for ($i = 0; $i -lt 45; $i++) {
    Start-Sleep -Seconds 1
    try { if ((Invoke-WebRequest "http://localhost:$Port/live" -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200) { $up = $true; break } } catch { }
}
if ($up) {
    Say "AltMount is running (hidden) at http://localhost:$Port, and will start by itself from now on." Green
} else {
    Say "The task started, but AltMount isn't answering yet. The end of its log:" Yellow
    Get-Content (Join-Path $Folder "console.log") -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Say "  $_" }
}
