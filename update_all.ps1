<#
  NZB Command Center - update everything.

  Keep this file in your NZB Command Center folder and right-click it > Run with PowerShell.
  It:
    1. downloads the newest program files from GitHub (main) into a temporary folder and checks them,
    2. asks you to close the desktop apps if they're open,
    3. stops the phone server,
    4. replaces only the files that changed; each old copy goes to backup\<date-time>\,
    5. starts the phone server again (through the startup task if there is one),
    6. lists what changed.
  Your settings and data (*.json, mobile_access_key.txt, logs, caches) are never touched.
  If a download or check fails, nothing is changed.
  To undo: copy the files from the newest backup\<date-time>\ folder back.

  -Unattended: for the Update button in the phone app (HAL starts it as a Windows task). No questions:
  open desktop apps aren't waited for (they use the new version next time you open them), there's no
  "Press Enter", everything is logged to update_all.log, and the outcome goes to update_result.json
  for HAL to report on the phone.
#>
param([switch]$Elevated, [switch]$Unattended)

$Repo = "F111ds/NZB-CC"; $Branch = "main"
$TaskName = "NZB Mobile Server"

# Only administrator can stop/start a phone server that runs as administrator (setup_startup.ps1).
$hasTask = [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue)
$HalTask = "HAL 9000 Server"
$hasHal = [bool](Get-ScheduledTask -TaskName $HalTask -ErrorAction SilentlyContinue)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (($hasTask -or $hasHal) -and -not $isAdmin -and $PSCommandPath -and -not $Unattended) {
    if ($Elevated) { Write-Host "Administrator rights were not granted." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated"
    exit
}

if ($Unattended -and $PSScriptRoot) { try { Start-Transcript -Path (Join-Path $PSScriptRoot "update_all.log") -Force | Out-Null } catch {} }
& {
$ErrorActionPreference = "Stop"
function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
# What happened, for HAL to tell you on the phone (update_result.json next to this script).
# The TV app (tv-launcher\NZB-CC-TV-Launcher.apk): installed on the TV whenever the TV doesn't have this exact
# file yet (remembered in tv_app_installed.txt), so it also catches up on a run where nothing else changed.
# The TV's settings and screensaver choice stay (install-on-tv.ps1 -UpdateOnly). Returns a sentence for the result.
function TvSync {
    $apk = Join-Path $Folder "tv-launcher\NZB-CC-TV-Launcher.apk"
    $script = Join-Path $Folder "tv-launcher\install-on-tv.ps1"
    if (-not (Test-Path $apk) -or -not (Test-Path $script)) { return "" }
    $hash = (Get-FileHash $apk -Algorithm SHA256).Hash
    $mark = Join-Path $Folder "tv_app_installed.txt"
    if ((Test-Path $mark) -and ((Get-Content $mark -Raw).Trim() -eq $hash)) { return "" }
    $tvHost = ""
    $hostFile = Join-Path $Folder "tv_host.txt"
    if (Test-Path $hostFile) { $tvHost = (Get-Content $hostFile -Raw).Trim() }
    if (-not $tvHost) {
        # The TV is where Kodi is: use Kodi's address from the NZB settings.
        try { $kurl = (Get-Content (Join-Path $Folder "nzb_manager_config.json") -Raw | ConvertFrom-Json).kodi_url; if ($kurl) { $tvHost = ([uri]$kurl).Host } } catch { }
    }
    if (-not $tvHost) { Say "A new TV app is ready: run tv-launcher\install-on-tv.ps1 once." Yellow; return " A new TV app is ready: run tv-launcher\install-on-tv.ps1 once." }
    Say "`nUpdating the app on the TV ($tvHost)..."
    $ErrorActionPreference = 'Continue'
    $tvOut = & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $script -TvHost $tvHost -UpdateOnly 2>&1 | Out-String
    $tvOk = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = 'Stop'
    Say $tvOut.Trim()
    if ($tvOk) {
        [IO.File]::WriteAllText($mark, $hash)
        Say "The TV app was updated too." Green
        return " The TV app was updated too."
    }
    # Say why, in the TV script's own words (its last line), instead of guessing.
    $why = @($tvOut -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -notmatch '^(At |\+ |CategoryInfo|FullyQualifiedErrorId|~)' })
    $why = if ($why.Count) { $why[-1] -replace '^.*?:\s*(?=[A-Z])', '' } else { "no answer from the TV" }
    if ($why.Length -gt 160) { $why = $why.Substring(0, 160) + "..." }
    Say "The TV app couldn't be updated: $why It'll try again next time." Yellow
    return " The TV app couldn't be updated: $why Run tv-launcher\install-on-tv.ps1 -TvHost $tvHost -UpdateOnly on the PC to see more; updates try again next time."
}

function Result($state, $msg, $files) {
    $dir = if ($script:Folder) { $script:Folder } else { $PSScriptRoot }
    if (-not $dir) { return }
    $v = $null; $mp = Join-Path $dir "mobile_server.py"
    if ((Test-Path $mp) -and ((Get-Content $mp -Raw) -match 'VERSION = "([\d.]+)"')) { $v = $Matches[1] }
    $h = $null; $hp = Join-Path $dir "hal_server.py"
    if ((Test-Path $hp) -and ((Get-Content $hp -Raw) -match 'VERSION = "([\d.]+)"')) { $h = $Matches[1] }
    $o = [ordered]@{ t = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds(); state = $state; message = "$msg"; version = $v; hal_version = $h; files = @($files) }
    try { [IO.File]::WriteAllText((Join-Path $dir "update_result.json"), ($o | ConvertTo-Json -Compress -Depth 3), (New-Object System.Text.UTF8Encoding $false)) } catch {}
}
if ($Unattended -and -not $isAdmin) { Result "failed" "The update needs administrator rights." @(); return }
# Programs run with 'Continue': under 'Stop', Windows PowerShell 5.1 turns a stderr line into a fatal error.
function Works($p) { if (-not $p) { return $false }; $ErrorActionPreference = 'Continue'; try { $o = & $p -c "print(42)" 2>$null; return ("$o".Trim() -eq "42") } catch { return $false } }
function Same($a, $b) {  # same content, ignoring Windows/Unix line endings
    if (-not (Test-Path $a) -or -not (Test-Path $b)) { return $false }
    $x = [IO.File]::ReadAllBytes($a); $y = [IO.File]::ReadAllBytes($b)
    if ($x.Length -eq $y.Length -and [Linq.Enumerable]::SequenceEqual($x, $y)) { return $true }
    if ($a -match '\.(apk|png|ico|jar)$') { return $false }
    return (([IO.File]::ReadAllText($a)) -replace "`r`n", "`n") -eq (([IO.File]::ReadAllText($b)) -replace "`r`n", "`n")
}
try {
    # --- Folder ---
    $Folder = $null
    foreach ($f in @($PSScriptRoot, (Get-Location).Path)) { if ($f -and (Test-Path (Join-Path $f "nzb_manager_config.json"))) { $Folder = $f; break } }
    if (-not $Folder -and -not $Unattended) { $Folder = (Read-Host "Path to your NZB Command Center folder").Trim('"', ' ') }
    $script:Folder = $Folder
    if (-not (Test-Path (Join-Path $Folder "nzb_manager_config.json"))) { throw "No nzb_manager_config.json in $Folder." }
    Say "NZB Command Center - update everything" Cyan
    Say "Folder: $Folder"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

    # --- Which files: the program files on GitHub (never tests, sources of the TV app, or settings) ---
    $paths = $null
    try {
        $tree = Invoke-RestMethod "https://api.github.com/repos/$Repo/git/trees/${Branch}?recursive=1" -Headers @{ "User-Agent" = "nzbcc-updater" }
        $paths = @($tree.tree | Where-Object { $_.type -eq "blob" } | ForEach-Object { $_.path })
    } catch { Say "Couldn't read the file list from GitHub; using the built-in list." Yellow }
    if (-not $paths) {
        $paths = @("mobile_server.py", "nzb_command_center.py", "media_server.py", "media_inspector.py", "kodi_client.py",
                   "system_command_center.py", "system_monitor.py", "app_icon.ico", "system_center_icon.ico", "README.md",
                   "update_phone_app.ps1", "update_all.ps1", "setup_startup.ps1", "hal_server.py", "setup_hal_startup.ps1",
                   "mobile_page.py", "mobile_icons.py", "app_page.py", "setup_autologon.ps1", "setup.ps1",
                   "setup_jellyfin_service.ps1", "install_arr_resume.ps1", "backup_settings.ps1")
    }
    $wanted = @($paths | Where-Object {
        ($_ -notmatch '/' -and $_ -match '\.(py|ps1|ico|md)$' -and $_ -ne 'CLAUDE.md') -or
        ($_ -match '^tv-launcher/[^/]+\.(ps1|apk|md)$') -or ($_ -match '^tv-launcher/tools/[^/]+$')
    })
    # The TV tools only if you have the tv-launcher folder.
    if (-not (Test-Path (Join-Path $Folder "tv-launcher"))) { $wanted = @($wanted | Where-Object { $_ -notmatch '^tv-launcher/' }) }

    # --- 1. Download everything to a temporary folder ---
    $stage = Join-Path $env:TEMP ("nzbcc-update-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $stage | Out-Null
    Say "`nDownloading $($wanted.Count) files..."
    foreach ($p in $wanted) {
        $dest = Join-Path $stage ($p -replace '/', '\')
        New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
        try { Invoke-WebRequest "https://raw.githubusercontent.com/$Repo/$Branch/$p" -OutFile $dest -UseBasicParsing -Headers @{ "Cache-Control" = "no-cache" } }
        catch { throw "Download of $p failed ($($_.Exception.Message)). Nothing was changed." }
        if ((Get-Item $dest).Length -eq 0) { throw "$p downloaded empty. Nothing was changed." }
    }

    # --- Check the Python files before touching anything ---
    $python = $null
    foreach ($c in "py", "python", "python3") { $cmd = Get-Command $c -ErrorAction SilentlyContinue; $p = if ($cmd) { $cmd.Source } else { $null }; if (Works $p) { $python = $p; break } }
    $pyFiles = @($wanted | Where-Object { $_ -match '\.py$' })
    if ($python) {
        foreach ($p in $pyFiles) {
            $ErrorActionPreference = 'Continue'
            $out = (& $python -m py_compile (Join-Path $stage ($p -replace '/', '\')) 2>&1 | Out-String).Trim(); $code = $LASTEXITCODE
            $ErrorActionPreference = 'Stop'
            if ($code -ne 0) { throw "$p has an error, so nothing was changed:`n$out" }
        }
        Say "Checked $($pyFiles.Count) Python files: OK" Green
    } else { Say "Couldn't run Python to check the files; skipping that check." Yellow }
    if ((Get-Content (Join-Path $stage "mobile_server.py") -Raw) -notmatch 'class MobileHandler') { throw "The download doesn't look right (maybe a GitHub sign-in page). Nothing was changed." }

    # --- What changed? ---
    $changed = @(); $new = @()
    foreach ($p in $wanted) {
        $src = Join-Path $stage ($p -replace '/', '\'); $dst = Join-Path $Folder ($p -replace '/', '\')
        if (-not (Test-Path $dst)) { $new += $p } elseif (-not (Same $src $dst)) { $changed += $p }
    }
    if (-not ($changed + $new)) {
        Say "`nEverything is already up to date." Green
        $tvMsg = TvSync
        Result $(if ($tvMsg -like "*updated too*") { "updated" } else { "uptodate" }) ("Everything is already up to date." + $tvMsg) @()
        return
    }
    Say "`nTo update: $($changed.Count) changed, $($new.Count) new"
    ($changed + $new) | ForEach-Object { Say "  $_" }

    # --- 2. Desktop apps must be closed (Windows keeps their files in use) ---
    $deskRx = 'nzb_command_center\.py|system_command_center\.py'
    while ($true) {
        $desk = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' OR Name = 'py.exe'" | Where-Object { $_.CommandLine -match $deskRx })
        if (-not $desk.Count) { break }
        if ($Unattended) { Say "Desktop apps are open: they use the new version next time you open them." Yellow; break }
        Say "`nPlease close NZB Command Center / System Command Center, then press Enter." Yellow
        $null = Read-Host
    }

    # --- 3. Stop the phone server (remember how it ran) ---
    $running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' OR Name = 'py.exe'" | Where-Object { $_.CommandLine -match 'mobile_server\.py' })
    $args2 = ""; $hidden = $false; $exe = $null
    if ($running.Count) {
        $exe = $running[0].ExecutablePath
        $hidden = ($running | Where-Object { $_.Name -like 'pythonw*' }).Count -gt 0
        if ($running[0].CommandLine -match 'mobile_server\.py"?\s*(.*)$') { $args2 = $Matches[1].Trim() }
    }
    if ($hasTask) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    # HAL 9000 too (restarted after the update)
    if ($hasHal) { Stop-ScheduledTask -TaskName $HalTask -ErrorAction SilentlyContinue }
    @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' OR Name = 'py.exe'" | Where-Object { $_.CommandLine -match 'hal_server\.py' }) |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if ($running.Count) { $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep 2; Say "`nStopped the phone server." }

    # --- 4. Back up and replace ---
    $backup = Join-Path $Folder ("backup\" + (Get-Date -Format "yyyy-MM-dd_HHmmss"))
    foreach ($p in $changed + $new) {
        $src = Join-Path $stage ($p -replace '/', '\'); $dst = Join-Path $Folder ($p -replace '/', '\')
        if (Test-Path $dst) {
            $bk = Join-Path $backup ($p -replace '/', '\')
            New-Item -ItemType Directory -Force -Path (Split-Path $bk) | Out-Null
            Copy-Item $dst $bk -Force
        }
        New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
        Copy-Item $src $dst -Force
    }
    Say "Updated $($changed.Count + $new.Count) files." Green
    if ($changed.Count) { Say "Old copies: $backup" }
    Remove-Item $stage -Recurse -Force -ErrorAction SilentlyContinue

    # --- HAL 9000 inside the phone server (mobile_server.py 3.12+): make sure its Python has psutil ---
    $embedded = [bool](Select-String -Path (Join-Path $Folder "mobile_server.py") -Pattern 'def start_embedded_hal' -Quiet)
    if ($embedded) {
        $pexe = if ($hasTask) { (Get-ScheduledTask -TaskName $TaskName).Actions[0].Execute } else { $exe }
        $pp = if ($pexe) { Join-Path (Split-Path $pexe) "python.exe" } else { $python }
        if ($pexe -and (Split-Path $pexe -Leaf) -match '^pyw?\.exe$') { $pp = Join-Path (Split-Path $pexe) "py.exe" }
        if (-not (Works $pp)) { $pp = $python }
        if ($pp) {
            $ErrorActionPreference = 'Continue'
            & $pp -c "import psutil" 2>$null
            if ($LASTEXITCODE -ne 0) { Say "Installing psutil (HAL 9000 needs it)..."; & $pp -m pip install --quiet psutil 2>&1 | Out-Null }
            $ErrorActionPreference = 'Stop'
        }
    }

    # --- 5. Start the phone server again ---
    if ($hasTask) {
        Start-ScheduledTask -TaskName $TaskName
        Say "Phone server started again (as administrator)." Green
    } elseif ($running.Count) {
        $run = if ($exe -and (Works (Join-Path (Split-Path $exe) "python.exe"))) { $exe } else { $python }
        if ($hidden -and $python -and $run -eq $python) { $w = Join-Path (Split-Path $python) "pythonw.exe"; if (Test-Path $w) { $run = $w } }
        if ($run) {
            $al = "`"$(Join-Path $Folder 'mobile_server.py')`""; if ($args2) { $al += " $args2" }
            Start-Process -FilePath $run -ArgumentList $al -WorkingDirectory $Folder
            Say "Phone server started again." Green
        } else { Say "Start the phone server the way you usually do." Yellow }
    } else { Say "The phone server wasn't running; start it the way you usually do." Yellow }

    if ($hasHal) {
        $phoneAuto = $hasTask -or (Test-Path (Join-Path ([Environment]::GetFolderPath("Startup")) "NZB Mobile Server.vbs"))
        if ($embedded -and $phoneAuto) {
            # HAL runs inside the phone server now: the separate task would only start a second copy.
            Unregister-ScheduledTask -TaskName $HalTask -Confirm:$false -ErrorAction SilentlyContinue
            Say "HAL 9000 now runs inside the phone server (one program, one startup, one log)." Green
            Say "Removed the separate '$HalTask' task. Your HAL icon keeps working; HAL is also at /hal on the phone server's address."
        } else { Start-ScheduledTask -TaskName $HalTask; Say "HAL 9000 started again." Green }
    }

    # --- 6. The TV app: when the TV doesn't have this version yet, put it on the TV ---
    $tvMsg = TvSync
    $v = if ((Get-Content (Join-Path $Folder "mobile_server.py") -Raw) -match 'VERSION = "([\d.]+)"') { $Matches[1] } else { "?" }
    Say "`nDone. Phone app version $v. Open the desktop apps again, and reopen the app on your phone." Cyan
    Result "updated" ("Updated $($changed.Count + $new.Count) files." + $tvMsg) @($changed + $new)
} catch {
    Say "`n$($_.Exception.Message)" Red
    Result "failed" $_.Exception.Message @()
}
}
if ($Unattended) { try { Stop-Transcript | Out-Null } catch {} } else { Read-Host "`nPress Enter to close" }
