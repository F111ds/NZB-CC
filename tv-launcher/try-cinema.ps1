<#
Puts the cinema preview on the TV BESIDE your launcher, without changing it.

The preview is a separate app (com.gathin.tvhome.preview, "Cinema (preview)" in the TV's app list). It never
takes the Home button, the screensaver or the remote's keys; your launcher, its settings and its Home stay
exactly as they are. Press Back in the preview to return to your home screen. To take it off again:

    powershell -ExecutionPolicy Bypass -File .\remove-cinema.ps1 -TvHost 192.168.68.62

Run on HAL9000, from this folder, in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\try-cinema.ps1 -TvHost 192.168.68.62

What it does:
  1. installs NZB-CC-Cinema-Preview.apk (a new app; the launcher is not touched)
  2. copies your launcher's settings (config.json: weather, exchange rate, Kodi row, dock apps) to the preview
  3. lets the preview see what Netflix / Prime Video are playing (notification access, for Continue watching)
  4. makes sure Home still belongs to your launcher, then opens the preview
#>
param(
    [Parameter(Mandatory = $true)][string]$TvHost,
    [string]$Apk = ''
)
if (-not $Apk) { $Apk = Join-Path $PSScriptRoot 'NZB-CC-Cinema-Preview.apk' }
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost
$main = 'com.gathin.tvhome'
$pkg = 'com.gathin.tvhome.preview'

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Step 'Setting up the TV tools (once)'
    py -3 -m venv tools\venv
    & .\tools\venv\Scripts\pip.exe install -q -r tools\requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Installing the TV tools failed (see above). Nothing was changed on the TV.' }
}
function Tv([string]$cmd) {
    $out = & $py -I tools\tv.py $cmd 2>&1
    return ($out | Out-String)
}

Step "Connecting to the TV at $TvHost"
$probe = & $py -I tools\tv.py --probe 2>&1 | Out-String
Write-Host $probe.Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not reach the TV. Check its IP address and that Network debugging is on. Nothing was changed.' }

Step 'Installing the cinema preview (beside your launcher)'
if (-not (Test-Path $Apk)) { throw "Missing $Apk" }
& $py -I tools\b64push.py $Apk /data/local/tmp/nzbcc-cinema.apk
if ($LASTEXITCODE -ne 0) { throw 'Copying the APK to the TV failed. Nothing was installed.' }
$out = Tv 'pm install -r /data/local/tmp/nzbcc-cinema.apk; rm -f /data/local/tmp/nzbcc-cinema.apk'
Write-Host $out.Trim()
if ($out -notmatch 'Success') { throw 'pm install failed (see above). Your launcher was not touched.' }

Step 'Copying your launcher settings to the preview'
$from = "/sdcard/Android/data/$main/files/config.json"
$to = "/sdcard/Android/data/$pkg/files"
Write-Host (Tv "mkdir -p $to && cp $from $to/config.json && echo copied || echo 'no settings to copy (defaults apply)'").Trim()

Step 'Continue watching: letting the preview see what apps are playing'
Write-Host (Tv "cmd notification allow_listener $pkg/com.gathin.tvhome.MediaWatch && echo allowed").Trim()
Tv "pm grant $pkg android.permission.ACCESS_FINE_LOCATION 2>/dev/null" | Out-Null

Step 'Keeping Home on your launcher, then opening the preview'
Tv "cmd package set-home-activity --user 0 $main/.MainActivity" | Out-Null
Write-Host (Tv "am start -n $pkg/com.gathin.tvhome.CinemaActivity").Trim()

Write-Host ''
Write-Host 'The preview is open on the TV. Back returns to your home screen; "Cinema (preview)" in the app list opens it again.' -ForegroundColor Green
Write-Host 'Continue watching fills in as you watch: play a few minutes of something in Netflix or Prime Video, then open the preview.' -ForegroundColor Green
Write-Host 'To remove it: powershell -ExecutionPolicy Bypass -File .\remove-cinema.ps1 -TvHost ' $TvHost -ForegroundColor Green
