<#
Adds a "Continue" button to your TV's dock, beside Netflix, Prime Video, YouTube and Kodi. It opens a
Continue watching panel over your home screen with what you were part-way through in Netflix and Prime Video.

Your launcher itself is not changed. The button is a small separate app (com.gathin.tvhome.preview); this
script installs it and adds it to the end of your dock. To go back exactly to how things were:

    powershell -ExecutionPolicy Bypass -File .\remove-continue.ps1 -TvHost 192.168.68.62

Run on HAL9000, from this folder, in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\try-continue.ps1 -TvHost 192.168.68.62
#>
param(
    [Parameter(Mandatory = $true)][string]$TvHost,
    [string]$Apk = ''
)
if (-not $Apk) { $Apk = Join-Path $PSScriptRoot 'NZB-CC-Continue-Preview.apk' }
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost
$main = 'com.gathin.tvhome'
$pkg = 'com.gathin.tvhome.preview'
$cfg = "/sdcard/Android/data/$main/files/config.json"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Step 'Setting up the TV tools (once)'
    py -3 -m venv tools\venv
    & .\tools\venv\Scripts\pip.exe install -q -r tools\requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Installing the TV tools failed (see above). Nothing was changed on the TV.' }
}
function Tv([string]$cmd) { $ErrorActionPreference = 'Continue'; return ((& $py -I tools\tv.py $cmd 2>&1) | Out-String) }

Step "Connecting to the TV at $TvHost"
$ErrorActionPreference = 'Continue'; $probe = & $py -I tools\tv.py --probe 2>&1 | Out-String; $code = $LASTEXITCODE; $ErrorActionPreference = 'Stop'; $global:LASTEXITCODE = $code
Write-Host $probe.Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not reach the TV. Check its IP address and that Network debugging is on. Nothing was changed.' }

Step 'Installing the Continue button app'
if (-not (Test-Path $Apk)) { throw "Missing $Apk" }
& $py -I tools\b64push.py $Apk /data/local/tmp/nzbcc-continue.apk
if ($LASTEXITCODE -ne 0) { throw 'Copying the APK to the TV failed. Nothing was installed.' }
$out = Tv 'pm install -r /data/local/tmp/nzbcc-continue.apk; rm -f /data/local/tmp/nzbcc-continue.apk'
Write-Host $out.Trim()
if ($out -notmatch 'Success') { throw 'pm install failed (see above). Your launcher was not touched.' }

Step 'Letting it see what Netflix and Prime Video are playing'
Write-Host (Tv "cmd notification allow_listener $pkg/com.gathin.tvhome.MediaWatch && echo allowed").Trim()

Step 'Adding Continue to the end of your dock'
# A copy of your launcher settings first; remove-continue.ps1 puts it back.
Tv "[ -f $cfg.before-continue ] || cp $cfg $cfg.before-continue" | Out-Null
$add = 's#"org.xbmc.kodi|Kodi"#"org.xbmc.kodi|Kodi", "com.gathin.tvhome.preview|Continue"#'
$has = Tv "grep -c 'com.gathin.tvhome.preview' $cfg"
if ($has.Trim() -eq '0') {
    Tv ("sed -i '" + $add + "' " + $cfg) | Out-Null
    $has = Tv "grep -c 'com.gathin.tvhome.preview' $cfg"
}
if ($has.Trim() -eq '0') {
    Write-Host 'Could not add it to home_apps automatically; add "Continue" to Home from the App library instead.' -ForegroundColor Yellow
} else {
    Write-Host 'added'
}

Step 'Reloading your home screen'
Tv "cmd package set-home-activity --user 0 $main/.MainActivity" | Out-Null
Tv "am force-stop $main; am start -a android.intent.action.MAIN -c android.intent.category.HOME" | Out-Null

Write-Host ''
Write-Host 'Done. "Continue" is at the end of your dock; Back closes the panel.' -ForegroundColor Green
Write-Host 'The panel fills in as you watch: play a few minutes of something in Netflix or Prime Video, then press Continue.' -ForegroundColor Green
Write-Host "To undo: powershell -ExecutionPolicy Bypass -File .\remove-continue.ps1 -TvHost $TvHost" -ForegroundColor Green
