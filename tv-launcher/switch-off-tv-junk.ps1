<#
Switches off the leftover tracking and promo apps on the Sony TV (BRAVIA 4K GB ATV3, Android 9).
Nothing is uninstalled: each app is disabled for the TV's user, stops running, and stays on the TV.
-Undo switches the same apps back on. A factory reset also brings them back.

Run on HAL9000, from this folder, in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\switch-off-tv-junk.ps1 -TvHost 192.168.68.62
    powershell -ExecutionPolicy Bypass -File .\switch-off-tv-junk.ps1 -TvHost 192.168.68.62 -Undo

It lists the apps and asks before changing anything.
#>
param(
    [Parameter(Mandatory = $true)][string]$TvHost,
    [switch]$Undo
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost

# Only these. Everything else on the TV is left exactly as it is.
$apps = [ordered]@{
    'com.sony.dtv.interactivetvutil'                     = 'Sony interactive-TV / viewing-data helper'
    'com.sony.dtv.interactivetvutil.output'              = 'Its output part'
    'com.sony.dtv.tvlaunchercustomizer'                  = 'Customises the old Google home (promoted app order); not used now'
    'com.sony.dtv.tvlaunchercustomizer.overlay.apporder' = 'Its app-order overlay'
    'com.sony.dtv.sonyselect.overlay'                    = 'Leftover of Sony Select (already off)'
    'com.google.android.backdrop'                        = 'Google screensaver (the photo screensaver replaces it)'
    'com.google.android.leanbacklauncher'                = 'The old Google home screen (not Home any more)'
}

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

$verb = if ($Undo) { 'switch back ON' } else { 'switch OFF' }
Write-Host ''
Write-Host "These apps will $verb (nothing is uninstalled):" -ForegroundColor Cyan
foreach ($k in $apps.Keys) { Write-Host ("  {0,-52} {1}" -f $k, $apps[$k]) }
Write-Host ''
$ok = Read-Host 'Go ahead? (y/n)'
if ($ok -notmatch '^(y|yes)$') { Write-Host 'Stopped. Nothing was changed on the TV.'; return }

$log = "tv-junk-$(if ($Undo) {'undo'} else {'off'})-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"
$lines = @()
foreach ($k in $apps.Keys) {
    if ($Undo) {
        $cmd = "pm enable $k 2>&1; cmd overlay enable $k 2>/dev/null; true"
    } else {
        # Overlay packages may refuse pm disable-user; turning the overlay off covers them.
        $cmd = "pm disable-user --user 0 $k 2>&1; cmd overlay disable $k 2>/dev/null; true"
    }
    $out = (& $py -I tools\tv.py $cmd 2>&1 | Out-String).Trim()
    if (-not $out) { $out = '(done)' }
    $line = "{0,-52} {1}" -f $k, ($out -replace '\s+', ' ')
    Write-Host $line
    $lines += $line
}
[IO.File]::WriteAllLines((Join-Path $PSScriptRoot $log), $lines)

Write-Host ''
Write-Host "Done. A record is in $log." -ForegroundColor Green
if (-not $Undo) {
    Write-Host 'To switch them all back on later:' -ForegroundColor Green
    Write-Host "  powershell -ExecutionPolicy Bypass -File .\switch-off-tv-junk.ps1 -TvHost $TvHost -Undo" -ForegroundColor Green
}
