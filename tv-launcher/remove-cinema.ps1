<#
Takes the cinema preview off the TV. Your launcher was never changed by it, so this is all it takes to be
exactly back where you were.

    powershell -ExecutionPolicy Bypass -File .\remove-cinema.ps1 -TvHost 192.168.68.62
#>
param([Parameter(Mandatory = $true)][string]$TvHost)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost
$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'The TV tools are not set up here (run try-cinema.ps1 or install-on-tv.ps1 once).' }
function Tv([string]$cmd) { return ((& $py -I tools\tv.py $cmd 2>&1) | Out-String) }

Write-Host (Tv 'pm uninstall com.gathin.tvhome.preview').Trim()
Tv 'cmd package set-home-activity --user 0 com.gathin.tvhome/.MainActivity' | Out-Null
Tv 'am start -a android.intent.action.MAIN -c android.intent.category.HOME' | Out-Null
Write-Host 'Removed. Your home screen is as it was.' -ForegroundColor Green
