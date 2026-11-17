<#
Everything waiting, in one go, after the latest files are copied in:
  1. restarts NZB Command Center's phone server (new: screensaver photos)
  2. installs the launcher on the TV (exchange rate, photo screensaver, sky, forecast, ...)
  3. the clean-up of 7 tracking/promo apps (lists them and asks y/n first)

Run on HAL9000, from this folder, in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\do-everything.ps1 -TvHost 192.168.68.62

Add -PhotosFolder "D:\Your\Pictures" to choose the screensaver's folder.
#>
param(
    [Parameter(Mandatory = $true)][string]$TvHost,
    [string]$PhotosFolder = ''
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost
$parent = Split-Path $PSScriptRoot -Parent
function Step($n, $text) { Write-Host "`n#### $n. $text" -ForegroundColor Magenta }

# ---- 1. phone server -------------------------------------------------------------------------------
Step 1 'Restarting the phone server'
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like '*mobile_server.py*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
$pyw = Join-Path $parent '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = 'pythonw' }
Start-Process $pyw -ArgumentList 'mobile_server.py' -WorkingDirectory $parent
$token = (Get-Content (Join-Path $parent 'mobile_access_key.txt') -Raw).Trim()
$up = $false
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    try {
        Invoke-RestMethod -Uri 'http://127.0.0.1:8765/api/tv/photos' -Headers @{ 'X-Mobile-Token' = $token } -TimeoutSec 60 | Out-Null
        $up = $true; break
    } catch { }
}
if (-not $up) { throw 'The phone server did not come back on port 8765. Start it by hand (pythonw mobile_server.py in the NZB Command Center folder) and run this again.' }
Write-Host 'The phone server is running the new version.' -ForegroundColor Green

# ---- 2. launcher -------------------------------------------------------------------------------------
Step 2 'Installing the launcher on the TV'
$installArgs = @('-ExecutionPolicy', 'Bypass', '-File', '.\install-on-tv.ps1', '-TvHost', $TvHost)
if ($PhotosFolder) { $installArgs += @('-PhotosFolder', $PhotosFolder) }
& powershell @installArgs
if ($LASTEXITCODE -ne 0) { throw 'The install stopped (see above). The clean-up was not run.' }

# ---- 3. clean-up --------------------------------------------------------------------------------------
Step 3 'Switching off the leftover tracking/promo apps'
& powershell -ExecutionPolicy Bypass -File .\switch-off-tv-junk.ps1 -TvHost $TvHost

Write-Host "`nAll done." -ForegroundColor Green
