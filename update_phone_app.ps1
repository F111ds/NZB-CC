<#
  NZB Command Center - phone app updater.

  Keep this file in your NZB Command Center folder (next to mobile_server.py) and
  right-click it > Run with PowerShell. It:
    1. downloads the newest mobile_server.py from GitHub (main) and checks it,
    2. stops the running phone server,
    3. keeps your old copy as mobile_server.old.py,
    4. installs the new one and starts the server again the same way it was running.
  Your settings, pairing code and saved searches are separate files and are not touched.
  If anything fails before step 4, nothing is changed.
  To go back: delete mobile_server.py and rename mobile_server.old.py to mobile_server.py.
  If setup_startup.ps1 made the phone server start as administrator, this asks for administrator
  rights too (only administrator can stop and restart it).
#>
param([switch]$Elevated)

$TaskName = "NZB Mobile Server"
$hasTask = [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($hasTask -and -not $isAdmin -and $PSCommandPath) {
    if ($Elevated) { Write-Host "Administrator rights were not granted." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated"
    exit
}

& {
$ErrorActionPreference = "Stop"
$Repo = "F111ds/NZB-CC"; $Branch = "main"
function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
function Works($p) { if (-not $p) { return $false }; try { $o = & $p -c "print(42)" 2>$null; return ("$o".Trim() -eq "42") } catch { return $false } }
try {
    # --- Find the NZB Command Center folder: this script's folder, else the current one, else ask ---
    $Folder = $null
    foreach ($f in @($PSScriptRoot, (Get-Location).Path)) {
        if ($f -and (Test-Path (Join-Path $f "nzb_manager_config.json"))) { $Folder = $f; break }
    }
    if (-not $Folder) { $Folder = (Read-Host "Path to your NZB Command Center folder").Trim('"', ' ') }
    if (-not (Test-Path (Join-Path $Folder "nzb_manager_config.json"))) { throw "No nzb_manager_config.json in $Folder." }
    $Target = Join-Path $Folder "mobile_server.py"
    $Backup = Join-Path $Folder "mobile_server.old.py"
    $Temp   = Join-Path $env:TEMP "mobile_server.new.py"
    Say "NZB Command Center - phone app updater" Cyan
    Say "Folder: $Folder"

    # --- 1. Download ---
    Say "Downloading the newest version..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest "https://raw.githubusercontent.com/$Repo/$Branch/mobile_server.py" -OutFile $Temp -UseBasicParsing -Headers @{ "Cache-Control" = "no-cache" }
    } catch {
        $dl = Join-Path $env:USERPROFILE "Downloads\mobile_server.py"
        if (-not (Test-Path $dl)) { throw "Download failed ($($_.Exception.Message)). Download mobile_server.py into your Downloads folder from https://github.com/$Repo/blob/$Branch/mobile_server.py and run this again." }
        Say "GitHub download failed; using Downloads\mobile_server.py instead." Yellow
        Copy-Item $dl $Temp -Force
    }

    # --- Check it's the real file ---
    $text = Get-Content $Temp -Raw -Encoding UTF8
    if ($text -notmatch 'class MobileHandler') { throw "That isn't the phone app (maybe a GitHub sign-in page). Nothing was changed." }
    if ($text -notmatch 'VERSION = "([\d.]+)"') { throw "The new file has no version number. Nothing was changed." }
    $new = $Matches[1]; $old = "none"
    if ((Test-Path $Target) -and ((Get-Content $Target -Raw -Encoding UTF8) -match 'VERSION = "([\d.]+)"')) { $old = $Matches[1] }
    Say "Installed: $old   New: $new" Green

    # --- Is the server running? Remember its options and whether it was hidden ---
    $running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' OR Name = 'py.exe'" | Where-Object { $_.CommandLine -match 'mobile_server\.py' })
    $args2 = ""; $hidden = $false
    if ($running.Count) {
        $hidden = ($running | Where-Object { $_.Name -like 'pythonw*' }).Count -gt 0
        if ($running[0].CommandLine -match 'mobile_server\.py"?\s*(.*)$') { $args2 = $Matches[1].Trim() }
    }

    # --- Find a Python that actually runs (Store Python only starts by its command name), check the file ---
    $python = $null
    $cands = @("py", "python", "python3")
    foreach ($r in $running) { if ($r.ExecutablePath) { $cands += (Join-Path (Split-Path $r.ExecutablePath) "python.exe") } }
    foreach ($c in $cands) { $cmd = Get-Command $c -ErrorAction SilentlyContinue; $p = if ($cmd) { $cmd.Source } else { $null }; if (Works $p) { $python = $p; break } }
    if ($python) {
        $out = & $python -m py_compile $Temp 2>&1
        if ($LASTEXITCODE -ne 0) { throw "The new file has an error, so nothing was changed: $out" }
    } else { Say "Couldn't run Python to test the file; skipping that check." Yellow }

    # --- 2. Stop, 3. back up, 4. replace ---
    if ($hasTask) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    if ($running.Count) { Say "Stopping the phone server..."; $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep 2 }
    elseif (-not $hasTask) { Say "The phone server wasn't running." }
    if (Test-Path $Target) { Copy-Item $Target $Backup -Force; Say "Old version saved as mobile_server.old.py" }
    Move-Item $Temp $Target -Force
    Say "Installed version $new" Green

    # --- Start it again: through the startup task (as administrator) if there is one ---
    if ($hasTask) {
        Start-ScheduledTask -TaskName $TaskName
        Say "Phone server started again (as administrator). Close and reopen the app on your phone." Green
        return
    }
    # Otherwise the same way it was running (hidden if it was hidden before).
    $run = $python
    if ($hidden -and $python) { $w = Join-Path (Split-Path $python) "pythonw.exe"; $wc = Get-Command pythonw -ErrorAction SilentlyContinue; if (Test-Path $w) { $run = $w } elseif ($wc) { $run = $wc.Source } }
    if ($run) {
        $al = "`"$Target`""; if ($args2) { $al += " $args2" }
        try { Start-Process -FilePath $run -ArgumentList $al -WorkingDirectory $Folder; Say "Phone server started. Close and reopen the app on your phone." Green }
        catch { Say "Installed, but couldn't start it ($($_.Exception.Message)). Start the server the way you usually do." Yellow }
    } else { Say "Installed. Start the server the way you usually do." Yellow }
} catch {
    Say "`n$($_.Exception.Message)" Red
}
}
Read-Host "`nPress Enter to close"
