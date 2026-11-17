<#
Puts the NZB-CC TV launcher on the TV: the CleanYourTV home screen with the weather in the top bar,
the Kodi row (the newest movies, one press to play) and the wallpaper off.

Run on HAL9000 (the PC that runs mobile_server.py), from this folder, in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\install-on-tv.ps1 -TvHost 192.168.1.50

What it does, in order:
  1. checks the phone server (mobile_server.py) answers on this PC, and reads its access code
  2. saves a copy of the TV's current launcher settings (config.json) in .\backup
  3. installs NZB-CC-TV-Launcher.apk over the launcher on the TV
  4. turns the wallpaper off, sets a single-row glass dock with the date beside the clock, and points the
     Kodi row (recently watched) at this PC
  5. restarts Home

Needs: Python 3 (py launcher) and Network debugging (ADB) on the TV - the same as CleanYourTV.
#>
param(
    [Parameter(Mandatory = $true)][string]$TvHost,
    [int]$ServerPort = 8765,
    # The folder of pictures for the screensaver (saved in tv_photos_folder.txt); default: your Pictures folder.
    [string]$PhotosFolder = '',
    [string]$Apk = '',
    # An AllRatesToday key for the live dollar-to-peso rate; saved in ..\fx_api_key.txt (not committed) so later
    # installs reuse it. Without one the TV shows the daily reference rate.
    [string]$FxKey = ''
)
# Windows PowerShell can leave $PSScriptRoot empty inside param() defaults, so the APK path is set here.
if (-not $Apk) { $Apk = Join-Path $PSScriptRoot 'NZB-CC-TV-Launcher.apk' }
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost
$pkg = 'com.gathin.tvhome'
$tvDir = "/sdcard/Android/data/$pkg/files"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }

# ---- tools ----------------------------------------------------------------------------------------
$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Step 'Setting up the TV tools (once)'
    # The py launcher can point at a Python that is gone (an old drive letter); fall back to python on PATH.
    $made = $false
    try { py -3 -m venv tools\venv 2>$null; $made = ($LASTEXITCODE -eq 0) -and (Test-Path $py) } catch { $made = $false }
    if (-not $made) { python -m venv tools\venv }
    if (-not (Test-Path $py)) { throw 'Could not create the Python environment (tried py and python). Install Python 3 from python.org and run this again. Nothing was changed on the TV.' }
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
if ($LASTEXITCODE -ne 0) {
    throw 'Could not reach the TV. Check its IP address, that Network debugging is on, and accept the prompt on the TV. Nothing was changed.'
}

# ---- the phone server on this PC ------------------------------------------------------------------
Step 'Checking NZB Command Center''s phone server on this PC'
$keyFile = Join-Path (Split-Path $PSScriptRoot -Parent) 'mobile_access_key.txt'
if (-not (Test-Path $keyFile)) {
    throw "No access code at $keyFile. Start the phone server once (python mobile_server.py --install-autostart) and run this again."
}
$token = (Get-Content $keyFile -Raw).Trim()
try {
    $row = Invoke-RestMethod -Uri "http://127.0.0.1:$ServerPort/api/tv/watched" -Headers @{ 'X-Mobile-Token' = $token } -TimeoutSec 60
    Write-Host "The phone server answers: $(@($row.items).Count) recently watched items for the Kodi row."
} catch {
    if ($_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 404) {
        throw "The phone server on port $ServerPort is an older version (no /api/tv/watched). Restart it from this folder's parent (pythonw mobile_server.py) and run this again. Nothing was changed on the TV."
    }
    throw "The phone server isn't answering on port $ServerPort ($($_.Exception.Message)). Start it (python mobile_server.py) and run this again. Nothing was changed on the TV."
}
# Screensaver photos: the folder the phone server reads them from.
if ($PhotosFolder) {
    if (-not (Test-Path $PhotosFolder -PathType Container)) { throw "No folder at $PhotosFolder. Nothing was changed on the TV." }
    $photosFile = Join-Path (Split-Path $PSScriptRoot -Parent) 'tv_photos_folder.txt'
    [IO.File]::WriteAllText($photosFile, (Resolve-Path $PhotosFolder).Path, (New-Object System.Text.UTF8Encoding $false))
}
try {
    $photos = Invoke-RestMethod -Uri "http://127.0.0.1:$ServerPort/api/tv/photos" -Headers @{ 'X-Mobile-Token' = $token } -TimeoutSec 120
    Write-Host "Screensaver: $($photos.count) photos in your '$($photos.folder)' folder."
    if ($photos.count -eq 0) { Write-Host "  (No pictures found. Run this again with -PhotosFolder 'D:\Some\Folder' to pick the folder.)" -ForegroundColor Yellow }
} catch {
    Write-Host 'Screensaver: the phone server has no photo list yet (restart it from the new mobile_server.py).' -ForegroundColor Yellow
}
# This PC's address on the network the TV is on.
$pcIp = & $py -I -c "import socket,sys; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect((sys.argv[1].split(':')[0], 5555)); print(s.getsockname()[0])" $TvHost
$server = "http://$($pcIp.Trim()):$ServerPort"
Write-Host "The TV will read the Kodi row from $server"

# ---- back up the TV's settings ----------------------------------------------------------------------
Step 'Saving a copy of the launcher settings on the TV'
New-Item -ItemType Directory -Force -Path backup | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = "backup\config-$stamp.json"
& $py -I tools\tv.py --pull "$tvDir/config.json" $backup 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0 -and (Test-Path $backup)) {
    Write-Host "Saved $backup"
    $cfgText = Get-Content $backup -Raw -Encoding UTF8
} else {
    Write-Host 'The TV has no theme settings yet; starting from an empty one.'
    $cfgText = '{}'
}

# ---- install --------------------------------------------------------------------------------------
Step 'Installing the launcher'
if (-not (Test-Path $Apk)) { throw "Missing $Apk" }
& $py -I tools\b64push.py $Apk /data/local/tmp/nzbcc-launcher.apk
if ($LASTEXITCODE -ne 0) { throw 'Copying the APK to the TV failed. Nothing was installed.' }
$out = Tv 'pm install -r /data/local/tmp/nzbcc-launcher.apk'
Write-Host $out.Trim()
if ($out -match 'INSTALL_FAILED_UPDATE_INCOMPATIBLE|signatures do not match') {
    Write-Host ''
    Write-Host 'The launcher on the TV was built with a different signing key, so Android will only take this one' -ForegroundColor Yellow
    Write-Host 'after the old one is removed. Your theme settings are saved and put back; the dock order, apps you' -ForegroundColor Yellow
    Write-Host 'added or hid in the App library, and module-card answers are kept inside the old app and will reset.' -ForegroundColor Yellow
    $ok = Read-Host 'Remove the old launcher and install this one? (y/n)'
    if ($ok -notmatch '^(y|yes)$') {
        Tv 'rm -f /data/local/tmp/nzbcc-launcher.apk' | Out-Null
        throw 'Stopped. Nothing was changed on the TV.'
    }
    $out = Tv "pm uninstall $pkg; pm install /data/local/tmp/nzbcc-launcher.apk"
    Write-Host $out.Trim()
}
Tv 'rm -f /data/local/tmp/nzbcc-launcher.apk' | Out-Null
if ($out -notmatch 'Success') { throw 'pm install failed (see above).' }
$ver = (Tv "dumpsys package $pkg | grep -m1 versionName").Trim()
Write-Host "Launcher on the TV now: $ver" -ForegroundColor Green

# ---- settings: wallpaper off, glass dock, Kodi row -----------------------------------------------
Step 'Turning the wallpaper off and pointing the Kodi row at this PC'
$cfg = $cfgText | ConvertFrom-Json
if ($null -eq $cfg) { $cfg = New-Object PSObject }
function SetKey($name, $value) {
    if ($cfg.PSObject.Properties[$name]) { $cfg.$name = $value }
    else { $cfg | Add-Member -NotePropertyName $name -NotePropertyValue $value }
}
SetKey 'wallpaper' $false
SetKey 'background_color' '#070B14'
SetKey 'dock_glass' $true
SetKey 'single_row' $true          # one row of apps along the bottom, nothing peeking below it
SetKey 'dock_band_all' $true       # the glass band runs behind the whole row
SetKey 'rows_below_dock' 0
SetKey 'date_below_clock' $false   # clock and date on one line
SetKey 'kodi_row_size' 5           # five posters: recently watched
SetKey 'dock_tile_scale' 0.5       # small app tiles in a compact row, centred
SetKey 'home_apps' @('com.netflix.ninja|Netflix', 'com.amazon.amazonvideo.livingroom|Prime Video', 'com.google.android.youtube.tv|YouTube', 'org.xbmc.kodi|Kodi')   # only these on Home; the rest are under the grid button
SetKey 'show_library_pill' $false  # no App library pill after the dock; Right past the last app or the grid button opens it
SetKey 'library_ok_opens' $true   # App library: OK opens the app; hold OK for Add to Home
SetKey 'sky_intensity' 70        # time-of-day sky behind Home (0 turns it off)
SetKey 'news' $false              # news ticker off (true turns it back on)
SetKey 'icon_only_inputs' $true    # Inputs as a round icon
SetKey 'vpn_app' 'com.nordvpn.android'   # the VPN lock: closed green while NordVPN is connected, open red while it is off
SetKey 'dock_icons_only' $true    # the dock shows app icons alone on the glass
SetKey 'kodi_row' $true
SetKey 'kodi_row_server' $server
SetKey 'kodi_row_token' $token
SetKey 'weather' $true
$fxKeyFile = Join-Path (Split-Path $PSScriptRoot -Parent) 'fx_api_key.txt'
if ($FxKey) { [IO.File]::WriteAllText($fxKeyFile, $FxKey.Trim(), (New-Object System.Text.UTF8Encoding $false)) }
elseif (Test-Path $fxKeyFile) { $FxKey = (Get-Content $fxKeyFile -Raw).Trim() }
if ($FxKey) {
    SetKey 'fx_key' $FxKey              # live mid-market rate (AllRatesToday)
    if (-not $cfg.PSObject.Properties['fx_refresh_minutes']) { SetKey 'fx_refresh_minutes' 10 }
    Write-Host 'Exchange rate: live (AllRatesToday key set)'
} else {
    Write-Host 'Exchange rate: daily (no AllRatesToday key; pass -FxKey to make it live)'
}
$newCfg = Join-Path $env:TEMP "nzbcc-config-$stamp.json"
[IO.File]::WriteAllText($newCfg, ($cfg | ConvertTo-Json -Depth 20), (New-Object System.Text.UTF8Encoding $false))
Tv "mkdir -p $tvDir" | Out-Null
& $py -I tools\b64push.py $newCfg "$tvDir/config.json"
if ($LASTEXITCODE -ne 0) { throw 'Writing the settings to the TV failed. The launcher is installed; run this again.' }
Remove-Item $newCfg -ErrorAction SilentlyContinue

# ---- Home ---------------------------------------------------------------------------------------
Step 'Restarting Home'
@(
    "pm grant $pkg android.permission.ACCESS_FINE_LOCATION 2>/dev/null; echo granted",
    "cmd package set-home-activity --user 0 $pkg/.MainActivity",
    "settings put secure screensaver_components $pkg/.PhotoDream",
    "settings put secure screensaver_enabled 1",
    "am force-stop $pkg",
    "am start -W -a android.intent.action.MAIN -c android.intent.category.HOME -n $pkg/.MainActivity"
) | & $py -I tools\tv.py --batch | Out-Null
$homeNow = (Tv 'cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME').Trim()
Write-Host "Home is now: $($homeNow.Split("`n")[-1])"
Write-Host ''
Write-Host 'Done. The Kodi row fills in within a few seconds; the weather appears in the top bar once it is read.' -ForegroundColor Green
Write-Host "Your previous settings are in $backup. To undo the look: put that file back with" -ForegroundColor Green
Write-Host "  .\tools\venv\Scripts\python.exe -I tools\b64push.py $backup $tvDir/config.json" -ForegroundColor Green
