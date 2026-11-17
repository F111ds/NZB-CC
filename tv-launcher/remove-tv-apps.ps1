<#
Lists the apps on the TV (the ones in the App library) and removes the ones you pick.

Run on HAL9000, from this folder, in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\remove-tv-apps.ps1 -TvHost 192.168.68.62

How it works
  1. Reads the TV's apps (nothing is changed yet).
  2. Opens a checklist window: tick the apps to remove and press OK. (Without the window, you type numbers.)
  3. Shows what will happen to each one and asks y/n.
  4. Apps you installed are uninstalled (reinstall them from the Play Store any time).
     Apps that came with the TV can't be uninstalled; they are switched off instead (they disappear and stop
     running, and come back with -Undo or a factory reset).
  Never offered: this launcher, Kodi, Netflix, Prime Video, YouTube, the Play Store, Settings and the TV's own
  system parts.

To switch back on the TV's own apps this script switched off:

    powershell -ExecutionPolicy Bypass -File .\remove-tv-apps.ps1 -TvHost 192.168.68.62 -Undo
#>
param(
    [Parameter(Mandatory = $true)][string]$TvHost,
    [switch]$Undo
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost
$disabledLog = Join-Path $PSScriptRoot 'tv-apps-switched-off.txt'   # the TV's own apps this script switched off

$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Setting up the TV tools (once)...' -ForegroundColor Cyan
    py -3 -m venv tools\venv
    & .\tools\venv\Scripts\pip.exe install -q -r tools\requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Installing the TV tools failed (see above). Nothing was changed on the TV.' }
}
$probe = & $py -I tools\tv.py --probe 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) {
    Write-Host $probe.Trim()
    throw 'Could not reach the TV. Check its IP address and that Network debugging is on. Nothing was changed.'
}
function Tv([string]$cmd) { return ((& $py -I tools\tv.py $cmd 2>&1 | Out-String).Trim()) }

# ---- undo ---------------------------------------------------------------------------------------------
if ($Undo) {
    if (-not (Test-Path $disabledLog)) { Write-Host 'Nothing to undo: this script has not switched off any of the TV''s apps.'; return }
    $pkgs = Get-Content $disabledLog | Where-Object { $_.Trim() } | Sort-Object -Unique
    Write-Host "Switching these back on:`n  $($pkgs -join "`n  ")"
    if ((Read-Host 'Go ahead? (y/n)') -notmatch '^(y|yes)$') { Write-Host 'Stopped. Nothing was changed.'; return }
    foreach ($p in $pkgs) { Write-Host ("{0,-45} {1}" -f $p, (Tv "pm enable $p")) }
    Remove-Item $disabledLog
    Write-Host 'Done.' -ForegroundColor Green
    return
}

# ---- what's on the TV -------------------------------------------------------------------------------
# Friendly names for packages seen on this TV; anything else shows its package name.
$names = @{
    'com.abide.magellantv' = 'MagellanTV'; 'com.ape.apps.networkbrowser' = 'Network Browser'
    'com.apple.atve.androidtv.appletv' = 'Apple TV'; 'com.cbs.tve' = 'CBS / Paramount'
    'com.cnn.mobile.android.tv' = 'CNN'; 'com.crackle.androidtv' = 'Crackle'
    'com.crunchyroll.crunchyroid' = 'Crunchyroll'; 'com.disney.disneyplus' = 'Disney+'
    'com.djandroid.jdroid.packagename' = 'Package Name Viewer'; 'com.famousbluemedia.yokee' = 'Yokee Karaoke'
    'com.google.android.inputmethod.japanese' = 'Google Japanese Input'; 'com.google.android.music' = 'Google Play Music'
    'com.gotokeep.yoga.intl' = 'Keep Yoga'; 'com.hulu.livingroomplus' = 'Hulu'
    'com.limelight' = 'Moonlight'; 'com.nordvpn.android' = 'NordVPN'
    'com.nousguide.android.rbtv' = 'Red Bull TV'; 'com.peacocktv.peacockandroid' = 'Peacock'
    'com.riatech.cookbook' = 'Cookbook'; 'com.tabdeveloper.tvoverlay' = 'TvOverlay'
    'com.tailscale.ipn' = 'Tailscale'; 'com.tm.replica' = 'Replica (screen mirroring)'
    'com.tubitv' = 'Tubi'; 'com.tvwebbrowser.v22' = 'TV Web Browser'
    'com.vimeo.android.videoapp' = 'Vimeo'; 'com.vixi.freedocumentary' = 'Free Documentaries'
    'com.yanka.mc.tv.prod.android' = 'MasterClass'; 'io.github.sumyabazar1.weather' = 'Weather'
    'org.localsend.localsend_app' = 'LocalSend'; 'org.videolan.vlc' = 'VLC'
    'tv.pluto.android' = 'Pluto TV'; 'tv.standard.nebula' = 'Nebula'
    'air.com.vudu.air.DownloaderTablet' = 'Vudu'; 'com.google.android.videos' = 'Google Play Movies & TV'
    'com.google.android.youtube.tvmusic' = 'YouTube Music'; 'com.google.android.play.games' = 'Google Play Games'
    'com.sony.dtv.timers' = 'Sony Timers & Clock'; 'com.google.android.apps.mediashell' = 'Chromecast built-in'
}
# Never offered: Home itself, the four dock apps, and the parts the TV needs.
$keep = @('com.gathin.tvhome', 'org.xbmc.kodi', 'com.netflix.ninja', 'com.amazon.amazonvideo.livingroom',
          'com.google.android.youtube.tv', 'com.android.vending', 'com.android.tv.settings',
          'com.google.android.apps.mediashell', 'com.google.android.katniss', 'com.google.android.gms')

Write-Host 'Reading the apps on the TV (nothing is changed yet)...' -ForegroundColor Cyan
$listing = @(
    'cmd package query-activities -a android.intent.action.MAIN -c android.intent.category.LEANBACK_LAUNCHER',
    'echo ==THIRD==',
    'pm list packages -3'
) | & $py -I tools\tv.py --batch 2>&1 | Out-String
$parts = $listing -split '==THIRD=='
$launchable = [regex]::Matches($parts[0], 'packageName=([A-Za-z0-9_.]+)') | ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique
$installed = @{}
foreach ($m in [regex]::Matches($parts[1], 'package:([A-Za-z0-9_.]+)')) { $installed[$m.Groups[1].Value] = $true }

$apps = foreach ($p in $launchable) {
    if ($keep -contains $p -or $p -like 'com.sony.dtv.*settings*' -or $p -like 'com.android.*') { continue }
    [pscustomobject]@{
        App     = if ($names.ContainsKey($p)) { $names[$p] } else { $p }
        Kind    = if ($installed[$p]) { 'You installed it (will be uninstalled)' } else { 'Came with the TV (will be switched off)' }
        Package = $p
    }
}
$apps = @($apps | Sort-Object App)
if ($apps.Count -eq 0) { throw 'The TV did not list any apps (see above). Nothing was changed.' }
Write-Host "Found $($apps.Count) apps you can remove (the dock apps, Kodi, Play Store and Settings are left out)."

# ---- pick -------------------------------------------------------------------------------------------
$picked = @()
$grid = Get-Command Out-GridView -ErrorAction SilentlyContinue
if ($grid) {
    Write-Host 'A window is opening: select the apps to remove (Ctrl+click for several), then press OK.' -ForegroundColor Cyan
    $picked = @($apps | Out-GridView -Title 'Pick the TV apps to remove, then press OK' -PassThru)
} else {
    for ($i = 0; $i -lt $apps.Count; $i++) { Write-Host ("{0,3}. {1,-32} {2}" -f ($i + 1), $apps[$i].App, $apps[$i].Kind) }
    $answer = Read-Host 'Numbers to remove (for example 1,4,7-9), or Enter for none'
    foreach ($piece in ($answer -split ',')) {
        $piece = $piece.Trim()
        if ($piece -match '^(\d+)-(\d+)$') { $a = [int]$Matches[1]; $b = [int]$Matches[2]; foreach ($n in $a..$b) { if ($n -ge 1 -and $n -le $apps.Count) { $picked += $apps[$n - 1] } } }
        elseif ($piece -match '^\d+$') { $n = [int]$piece; if ($n -ge 1 -and $n -le $apps.Count) { $picked += $apps[$n - 1] } }
    }
    $picked = @($picked | Sort-Object Package -Unique)
}
if ($picked.Count -eq 0) { Write-Host 'Nothing picked. Nothing was changed on the TV.'; return }

Write-Host ''
Write-Host 'These will be removed:' -ForegroundColor Yellow
foreach ($a in $picked) { Write-Host ("  {0,-32} {1}" -f $a.App, $a.Kind) }
if ((Read-Host "Remove these $($picked.Count) apps? (y/n)") -notmatch '^(y|yes)$') { Write-Host 'Stopped. Nothing was changed on the TV.'; return }

# ---- remove -----------------------------------------------------------------------------------------
$record = "tv-apps-removed-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"
$lines = @()
foreach ($a in $picked) {
    if ($installed[$a.Package]) {
        $out = Tv "pm uninstall $($a.Package)"
        $what = 'uninstalled'
    } else {
        $out = Tv "pm disable-user --user 0 $($a.Package)"
        $what = 'switched off'
        if ($out -match 'disabled') { Add-Content -Path $disabledLog -Value $a.Package }
    }
    $ok = $out -match 'Success|disabled'
    $line = "{0,-32} {1,-13} {2}" -f $a.App, $what, $(if ($ok) { 'OK' } else { "FAILED: $out" })
    Write-Host $line -ForegroundColor $(if ($ok) { 'Green' } else { 'Red' })
    $lines += $line
}
[IO.File]::WriteAllLines((Join-Path $PSScriptRoot $record), $lines)
Write-Host ''
Write-Host "Done. A record is in $record." -ForegroundColor Green
Write-Host 'Uninstalled apps can be reinstalled from the Play Store. To bring back the TV''s own apps that were switched off:'
Write-Host "  powershell -ExecutionPolicy Bypass -File .\remove-tv-apps.ps1 -TvHost $TvHost -Undo"
