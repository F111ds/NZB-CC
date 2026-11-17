<#
Takes the Continue button off the TV: uninstalls it and puts your launcher settings back exactly as they were
before try-continue.ps1.

    powershell -ExecutionPolicy Bypass -File .\remove-continue.ps1 -TvHost 192.168.68.62
#>
param([Parameter(Mandatory = $true)][string]$TvHost)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost
$main = 'com.gathin.tvhome'
$cfg = "/sdcard/Android/data/$main/files/config.json"
$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'The TV tools are not set up here (run try-continue.ps1 or install-on-tv.ps1 once).' }
function Tv([string]$cmd) { $ErrorActionPreference = 'Continue'; return ((& $py -I tools\tv.py $cmd 2>&1) | Out-String) }

Write-Host (Tv 'pm uninstall com.gathin.tvhome.preview').Trim()
Write-Host (Tv "if [ -f $cfg.before-continue ]; then cp $cfg.before-continue $cfg && rm $cfg.before-continue && echo 'settings restored'; else echo 'no saved settings (nothing to restore)'; fi").Trim()
Tv "cmd package set-home-activity --user 0 $main/.MainActivity" | Out-Null
Tv "am force-stop $main; am start -a android.intent.action.MAIN -c android.intent.category.HOME" | Out-Null
Write-Host 'Removed. Your home screen is as it was.' -ForegroundColor Green
