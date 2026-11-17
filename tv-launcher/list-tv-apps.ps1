<#
Lists what is installed on the TV (so the ad, tracking and junk apps can be picked out before anything is
switched off) and how its sound output is set up (for a TV speakers / audio system switch on Home). READ-ONLY: it only asks the TV questions; nothing on the TV is changed.

Run on HAL9000, from this folder, in PowerShell:

    powershell -ExecutionPolicy Bypass -File .\list-tv-apps.ps1 -TvHost 192.168.68.62

It writes tv-apps-<date>.txt here and copies the same text to the clipboard, ready to paste.
#>
param(
    [Parameter(Mandatory = $true)][string]$TvHost
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$env:TV_HOST = $TvHost

$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    Write-Host 'Setting up the TV tools (once)...' -ForegroundColor Cyan
    py -3 -m venv tools\venv
    & .\tools\venv\Scripts\pip.exe install -q -r tools\requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Installing the TV tools failed (see above). Nothing was changed on the TV.' }
}

Write-Host "Connecting to the TV at $TvHost (accept 'Allow USB debugging?' on the TV if it asks)..." -ForegroundColor Cyan
$ErrorActionPreference = 'Continue'; $probe = & $py -I tools\tv.py --probe 2>&1 | Out-String; $code = $LASTEXITCODE; $ErrorActionPreference = 'Stop'; $global:LASTEXITCODE = $code
if ($LASTEXITCODE -ne 0) {
    Write-Host $probe.Trim()
    throw 'Could not reach the TV. Check its IP address and that Network debugging is on. Nothing was changed.'
}

# Every command below only reads (getprop, pm list, settings get/list, dumpsys).
$commands = @(
    'echo "== TV"; getprop ro.product.manufacturer; getprop ro.product.model; echo "Android $(getprop ro.build.version.release)"; getprop ro.build.fingerprint',
    'echo "== Home app now"; cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME | tail -n 1',
    'echo "== System apps, switched on"; pm list packages -s -e | sed "s/^package://" | sort',
    'echo "== Apps already switched off"; pm list packages -d | sed "s/^package://" | sort',
    'echo "== Apps you installed"; pm list packages -3 | sed "s/^package://" | sort',
    'echo "== Sound output settings (TV speakers / audio system)"; for t in global system secure; do settings list $t | grep -i -E "speaker|audio|sound|arc|cec|hdmi_control|system_audio|output" | sed "s/^/$t: /"; done; echo "(end)"',
    'echo "== HDMI control"; dumpsys hdmi_control | grep -i -E "system audio|arc|audio system|speaker|power" | head -n 40',
    'echo "== Sound settings screens"; cmd package query-activities -a android.settings.SOUND_SETTINGS | grep -i -E "name=|packageName" | head -n 10',
    'echo "== Settings that may relate to ads, usage data or viewing data"; settings list global | grep -i -E "ad_|ads|usage|diagnos|analytic|samba|acr|interactive|promo|recommend" ; settings list secure | grep -i -E "ad_|ads|usage|diagnos|analytic|samba|acr|interactive|promo|recommend"; echo "(end)"'
)
# Sent on stdin (batch mode), one command per line: Windows PowerShell mangles quotes inside arguments.
$ErrorActionPreference = 'Continue'; $out = $commands | & $py -I tools\tv.py --batch 2>&1 | Out-String; $code = $LASTEXITCODE; $ErrorActionPreference = 'Stop'; $global:LASTEXITCODE = $code
if ($LASTEXITCODE -ne 0 -and -not $out.Trim()) { throw 'The TV did not answer the questions (see above). Nothing was changed.' }
$text = $out.TrimEnd() + "`r`n"

$file = "tv-apps-$(Get-Date -Format 'yyyyMMdd-HHmmss').txt"
[IO.File]::WriteAllText((Join-Path $PSScriptRoot $file), $text, (New-Object System.Text.UTF8Encoding $false))
try { Set-Clipboard -Value $text; $copied = $true } catch { $copied = $false }

$count = ($text -split "`n" | Where-Object { $_ -match '^[a-z][a-z0-9_]*(\.[a-zA-Z0-9_]+)+\s*$' }).Count
Write-Host ''
Write-Host "Done. Nothing on the TV was changed. Found $count app packages." -ForegroundColor Green
Write-Host "Saved to $file in this folder."
if ($copied) { Write-Host 'The list is also on the clipboard: paste it into the chat.' -ForegroundColor Green }
else { Write-Host "Open $file and paste its contents into the chat." }
