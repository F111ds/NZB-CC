<#
  NZB Command Center - start Jellyfin when Windows starts (before anyone logs in).

  Right-click > Run with PowerShell (it asks for administrator rights once), or paste the one-line
  command you were given. It:
    1. finds your Jellyfin (program and data folder) from the copy that is running now,
    2. creates the Task Scheduler task "Jellyfin Server (at startup)": starts jellyfin.exe with the
       same data folder at Windows startup, under YOUR account (no password stored, so it keeps
       access to its files), and starts it again within 5 minutes if it ever stops,
    3. takes the Jellyfin tray app out of startup (so two copies don't fight over port 8096) and
       writes down exactly what it changed, for undoing,
    4. switches over now and checks Jellyfin answers.
  Nothing is moved or reinstalled; your libraries, users and watched history stay where they are.
  To undo: run this with -Undo.
#>
param([switch]$Elevated, [string]$ForUser, [switch]$Undo)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    if ($Elevated) { Write-Host "Administrator rights were not granted." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
    $a = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated -ForUser `"$env:USERDOMAIN\$env:USERNAME`""
    if ($Undo) { $a += " -Undo" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $a
    exit
}

& {
$ErrorActionPreference = "Stop"
$TaskName = "Jellyfin Server (at startup)"
$UndoFile = Join-Path $env:ProgramData "NZB-CC\jellyfin_startup_undo.json"
function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
function Up { try { $null = Invoke-WebRequest "http://127.0.0.1:8096/System/Info/Public" -UseBasicParsing -TimeoutSec 4; return $true } catch { return $false } }
$user = if ($ForUser) { $ForUser } else { "$env:USERDOMAIN\$env:USERNAME" }
try {
    if ($Undo) {
        # ---------------- Undo: back to the tray app at logon ----------------
        Say "Jellyfin: back to starting with the tray app at logon" Cyan
        if (-not (Test-Path $UndoFile)) { throw "Nothing to undo (no $UndoFile)." }
        $u = Get-Content $UndoFile -Raw | ConvertFrom-Json
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Get-Process -Name "jellyfin" -ErrorAction SilentlyContinue | Stop-Process -Force
        foreach ($r in @($u.run)) { if ($r) { New-ItemProperty -Path $r.key -Name $r.name -Value $r.value -PropertyType String -Force | Out-Null; Say "Restored startup entry: $($r.name)" } }
        foreach ($s in @($u.shortcuts)) { if ($s -and (Test-Path $s.backup)) { Move-Item $s.backup $s.original -Force; Say "Restored shortcut: $($s.original)" } }
        foreach ($t in @($u.tasks)) { if ($t) { Enable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue | Out-Null; Say "Re-enabled task: $t" } }
        if ($u.tray -and (Test-Path $u.tray)) { Start-Process explorer.exe -ArgumentList "`"$($u.tray)`"" }
        Remove-Item $UndoFile -Force
        Say "Done: Jellyfin starts with the tray app when you log in, as before." Green
        return
    }

    Say "Jellyfin: start when Windows starts" Cyan
    # ---------------- 1. Find Jellyfin ----------------
    $proc = Get-CimInstance Win32_Process -Filter "Name='jellyfin.exe'" | Select-Object -First 1
    if (-not $proc) { throw "Jellyfin isn't running. Start it the way you usually do (the tray app), then run this again." }
    $exe = $proc.ExecutablePath
    $dataDir = if ($proc.CommandLine -match '(?:--datadir|-d)\s+"([^"]+)"') { $Matches[1] } elseif ($proc.CommandLine -match '(?:--datadir|-d)\s+(\S+)') { $Matches[1] } else { $null }
    if (-not $dataDir) { throw "Couldn't see which data folder Jellyfin uses. Nothing was changed." }
    if (-not (Test-Path (Join-Path $dataDir "config\system.xml"))) { throw "Jellyfin's settings aren't in $dataDir as expected. Nothing was changed." }
    $tray = Get-Process -Name "Jellyfin.Windows.Tray" -ErrorAction SilentlyContinue | Where-Object { $_.Path } | Select-Object -First 1 -ExpandProperty Path
    Say "Program: $exe"
    Say "Data:    $dataDir"

    # ---------------- 2. The startup task ----------------
    $action = New-ScheduledTaskAction -Execute $exe -Argument "--datadir `"$dataDir`"" -WorkingDirectory (Split-Path $exe)
    $atBoot = New-ScheduledTaskTrigger -AtStartup
    $atBoot.Delay = "PT30S"   # let the disks and network come up
    # A second trigger every 5 minutes: starts Jellyfin again if it has stopped (while it runs, the
    # "ignore new" setting makes it do nothing). Also covers "Restart" from Jellyfin's dashboard.
    $watch = New-ScheduledTaskTrigger -Once -At ((Get-Date).AddMinutes(5)) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 3650)
    # S4U: runs whether or not you're logged in, as you, without storing your password.
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger @($atBoot, $watch) -Principal $principal -Settings $settings `
        -Description "Starts Jellyfin ($exe, data in $dataDir) when Windows starts, as $user. Made by NZB Command Center's setup_jellyfin_service.ps1." -Force -ErrorAction Stop | Out-Null  # Task Scheduler cmdlets ignore the script-wide Stop
    Say "Task '$TaskName' created: starts at Windows startup, as $user." Green

    # ---------------- 3. Tray app out of startup (remember everything for -Undo) ----------------
    $undo = @{ run = @(); shortcuts = @(); tasks = @(); tray = $tray }
    foreach ($key in "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run", "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run", "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run") {
        $props = Get-ItemProperty $key -ErrorAction SilentlyContinue
        if (-not $props) { continue }
        foreach ($p in $props.PSObject.Properties) {
            if ($p.Name -notlike "PS*" -and "$($p.Value)" -match 'jellyfin') {
                $undo.run += @{ key = $key; name = $p.Name; value = "$($p.Value)" }
                Remove-ItemProperty -Path $key -Name $p.Name
                Say "Removed tray startup entry: $($p.Name)"
            }
        }
    }
    $bk = Join-Path $env:ProgramData "NZB-CC\startup-backup"
    foreach ($dir in [Environment]::GetFolderPath("Startup"), [Environment]::GetFolderPath("CommonStartup")) {
        Get-ChildItem $dir -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'jellyfin' } | ForEach-Object {
            New-Item -ItemType Directory -Force -Path $bk | Out-Null
            $dest = Join-Path $bk $_.Name
            Move-Item $_.FullName $dest -Force
            $undo.shortcuts += @{ original = $_.FullName; backup = $dest }
            Say "Moved tray startup shortcut out of Startup: $($_.Name)"
        }
    }
    Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match 'jellyfin' -and $_.TaskName -ne $TaskName -and $_.State -ne 'Disabled' } | ForEach-Object {
        Disable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath | Out-Null
        $undo.tasks += $_.TaskName
        Say "Disabled the old task: $($_.TaskName)"
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $UndoFile) | Out-Null
    $undo | ConvertTo-Json -Depth 5 | Set-Content $UndoFile -Encoding UTF8

    # ---------------- 4. Switch over now ----------------
    Say "Switching over (Jellyfin is offline for a few seconds)..."
    Get-Process -Name "Jellyfin.Windows.Tray" -ErrorAction SilentlyContinue | Stop-Process -Force
    Get-Process -Name "jellyfin" -ErrorAction SilentlyContinue | Stop-Process -Force
    Start-Sleep 3
    Start-ScheduledTask -TaskName $TaskName
    $ok = $false; foreach ($i in 1..60) { Start-Sleep 2; if (Up) { $ok = $true; break } }
    if ($ok) {
        Say "`nDone. Jellyfin is running from the startup task and will start with Windows, even before anyone logs in." Green
        Say "The tray icon is gone; manage Jellyfin in the browser (http://192.168.68.60:8096) or the Jellyfin tab of the phone app."
    } else {
        Say "`nThe task started, but Jellyfin isn't answering yet. Give it a minute and check http://127.0.0.1:8096." Yellow
        Say "If it doesn't come up, run this script with -Undo to go back to the tray app."
    }
    Say "To undo later: run setup_jellyfin_service.ps1 -Undo"
} catch {
    Say "`n$($_.Exception.Message)" Red
}
}
Read-Host "`nPress Enter to close"
