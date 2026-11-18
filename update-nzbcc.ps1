<#
Updates NZB Command Center on this PC from GitHub in one go:
  1. downloads the latest files of the working branch
  2. copies in mobile_server.py and the tv-launcher folder (your config, access code and settings are not touched)
  3. restarts the phone server (hidden) and checks it answers
  4. with -Tv, also installs the latest launcher on the TV

Run from the NZB Command Center folder, in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\update-nzbcc.ps1
    powershell -ExecutionPolicy Bypass -File .\update-nzbcc.ps1 -Tv
#>
param(
    [switch]$Tv,
    [string]$TvHost = '192.168.68.62',
    [string]$Branch = 'claude/stoic-mayer-qr9489'
)
$ErrorActionPreference = 'Stop'
$nzb = $PSScriptRoot
if (-not (Test-Path (Join-Path $nzb 'nzb_manager_config.json'))) {
    throw "Put this script in the NZB Command Center folder (the one with nzb_manager_config.json) and run it from there."
}

# ---- 1. download ----------------------------------------------------------------------------------
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$zip = Join-Path $env:TEMP "nzbcc-$stamp.zip"
$dir = Join-Path $env:TEMP "nzbcc-$stamp"
Write-Host "Downloading the latest files ($Branch)..." -ForegroundColor Cyan
Invoke-WebRequest "https://github.com/F111ds/NZB-CC/archive/refs/heads/$Branch.zip" -OutFile $zip
Expand-Archive $zip $dir -Force
$src = (Get-ChildItem $dir -Directory | Select-Object -First 1).FullName

# ---- 2. copy ----------------------------------------------------------------------------------------
Copy-Item (Join-Path $src 'mobile_server.py') (Join-Path $nzb 'mobile_server.py') -Force
Copy-Item (Join-Path $src 'tv-launcher\*') (Join-Path $nzb 'tv-launcher') -Recurse -Force
Copy-Item (Join-Path $src 'update-nzbcc.ps1') (Join-Path $nzb 'update-nzbcc.ps1') -Force   # this script, kept current
Write-Host 'Copied in the new phone server and TV launcher files.' -ForegroundColor Green

# ---- 3. restart the phone server ----------------------------------------------------------------
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like '*mobile_server*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
$pyw = (Get-Command pythonw -ErrorAction SilentlyContinue).Source
if (-not $pyw) { $pyw = (Get-Command pyw -ErrorAction SilentlyContinue).Source }
if (-not $pyw) { throw 'Could not find pythonw or pyw. Start the server by hand: python mobile_server.py' }
Start-Process $pyw -ArgumentList 'mobile_server.py' -WorkingDirectory $nzb
$token = (Get-Content (Join-Path $nzb 'mobile_access_key.txt') -Raw).Trim()
$up = $false
foreach ($i in 1..20) {
    Start-Sleep -Seconds 1
    try { Invoke-RestMethod 'http://127.0.0.1:8765/api/tv' -Headers @{ 'X-Mobile-Token' = $token } -TimeoutSec 30 | Out-Null; $up = $true; break } catch { }
}
if ($up) { Write-Host 'The phone server is running the new version.' -ForegroundColor Green }
else { throw 'The phone server did not start. Run "python mobile_server.py" in this folder to see why.' }

# ---- 4. the TV (optional) ------------------------------------------------------------------------
if ($Tv) {
    & powershell -ExecutionPolicy Bypass -File (Join-Path $nzb 'tv-launcher\install-on-tv.ps1') -TvHost $TvHost
}

Remove-Item $zip -ErrorAction SilentlyContinue
Remove-Item $dir -Recurse -ErrorAction SilentlyContinue
Write-Host "`nDone. Reopen the phone app to see the changes." -ForegroundColor Green
if (-not $Tv) { Write-Host 'To also update the TV: powershell -ExecutionPolicy Bypass -File .\update-nzbcc.ps1 -Tv' }
