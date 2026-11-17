<#
Checks why the Continue panel is empty. Changes nothing on the TV; it only reads, and saves what it finds to
continue-check.txt in this folder (Claude reads it from there).

Best run while a Netflix show is PAUSED: start something in Netflix, pause it, then run:

    powershell -ExecutionPolicy Bypass -File .\check-continue.ps1 -TvHost 192.168.68.62
#>
param([Parameter(Mandatory = $true)][string]$TvHost)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost
$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'The TV tools are not set up here (run try-continue.ps1 once).' }
function Tv([string]$cmd) { return ((& $py -I tools\tv.py $cmd 2>&1) | Out-String) }

$out = New-Object System.Text.StringBuilder
function Section($title, $cmd) {
    [void]$out.AppendLine("===== $title")
    [void]$out.AppendLine("$ $cmd")
    [void]$out.AppendLine((Tv $cmd).TrimEnd())
    [void]$out.AppendLine('')
}

Section 'Android version' 'getprop ro.build.version.release; getprop ro.build.version.sdk'
Section 'Continue app installed' 'pm list packages | grep -E "gathin|netflix|amazon"'
Section 'Notification access (MediaWatch should be listed)' 'settings get secure enabled_notification_listeners'
Section 'Listener bound?' 'dumpsys notification | grep -iE "MediaWatch|enabled_listeners|Live notification listeners" -A3 | head -40'
Section 'Media sessions (what Netflix reports)' 'dumpsys media_session'
Section 'Watch Next list (may say no permission)' 'content query --uri content://android.media.tv/watch_next_program --projection package_name:title:watch_next_type 2>&1 | head -40'
Section 'Continue app log' 'logcat -d -t 400 | grep -iE "MediaWatch|ContinueStore|gathin.tvhome.preview|MediaSession" | tail -60'

$file = Join-Path $PSScriptRoot 'continue-check.txt'
[IO.File]::WriteAllText($file, $out.ToString())
Write-Host "Saved to $file. Tell Claude it's there." -ForegroundColor Green
