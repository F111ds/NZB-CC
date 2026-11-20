<#
Takes real pictures of HAL on the TV: the TV's home screen, HAL's screen and all five detail pages
(The PC, Network, Uptime Kuma, Raspberry Pi, Downloads). It presses the remote's keys itself, so leave the
remote alone while it runs (about a minute). The pictures go in a new folder next to this one, which opens
when it's done.

Run on HAL9000, from this folder, in PowerShell, with the TV on:

    powershell -ExecutionPolicy Bypass -File .\tv-screenshots.ps1

Options:
    -TvHost 1.2.3.4   the TV's address (default: the one the TV installer remembered)
    -Wait 5           seconds to let each page draw before its picture (default 5)
#>
param(
    [string]$TvHost = '',
    [int]$Wait = 5
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$root = Split-Path $PSScriptRoot -Parent

if (-not $TvHost) {
    $hostFile = Join-Path $root 'tv_host.txt'
    if (Test-Path $hostFile) { $TvHost = (Get-Content $hostFile -Raw).Trim() }
}
if (-not $TvHost) { $TvHost = (Read-Host "The TV's address (e.g. 192.168.68.62)").Trim() }
$env:TV_HOST = $TvHost

$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'The TV tools are not set up yet. Run install-on-tv.ps1 once first.' }

$out = Join-Path $root ('tv-screenshots-' + (Get-Date -Format 'yyyy-MM-dd-HHmm'))
New-Item -ItemType Directory -Force -Path $out | Out-Null

function Tv([string]$cmd) {
    # Single quotes only inside $cmd: Windows PowerShell drops inner double quotes when it starts Python.
    $ErrorActionPreference = 'Continue'
    $null = & $py -I tools\tv.py $cmd 2>&1
}
function Shot([string]$name, [string[]]$keys) {
    $file = Join-Path $out $name
    $ErrorActionPreference = 'Continue'
    $a = @('-I', 'tools\tv.py', '--screenshot', $file) + $keys + @('--wait', "$Wait")
    $res = & $py @a 2>&1 | Out-String
    if ($LASTEXITCODE -eq 0 -and (Test-Path $file)) { Write-Host "  $name" -ForegroundColor Green }
    else { Write-Host "  $name - no picture: $($res.Trim() -split "`n" | Select-Object -Last 1)" -ForegroundColor Yellow }
}

Write-Host "Connecting to the TV at $TvHost..." -ForegroundColor Cyan
$ErrorActionPreference = 'Continue'
$probe = & $py -I tools\tv.py --probe 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { throw "Couldn't reach the TV: $($probe.Trim()). Is it on?" }

Write-Host "Taking pictures (leave the remote alone for a minute)..." -ForegroundColor Cyan
Tv 'input keyevent KEYCODE_WAKEUP'
Shot '0-home-screen.png' @('HOME')
Tv 'am start -n com.gathin.tvhome/.HalActivity -f 0x10000000'
Start-Sleep -Seconds 4
Shot '1-hal-screen.png' @()
Shot '2-details-the-pc.png' @('DPAD_DOWN')
Shot '3-details-network.png' @('DPAD_RIGHT')
Shot '4-details-uptime-kuma.png' @('DPAD_RIGHT')
Shot '5-details-raspberry-pi.png' @('DPAD_RIGHT')
Shot '6-details-downloads.png' @('DPAD_RIGHT')
Tv 'input keyevent KEYCODE_BACK'
Tv 'input keyevent KEYCODE_HOME'

$n = @(Get-ChildItem $out -Filter *.png).Count
Write-Host "`nDone: $n pictures in $out" -ForegroundColor Green
if ($n) { Start-Process explorer.exe $out }
