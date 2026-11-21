<#
  NZB Command Center - CPU temperatures (LibreHardwareMonitor).

  Windows doesn't give apps the CPU temperature or fan speeds. HAL reads them from LibreHardwareMonitor's
  little web server on this PC (http://localhost:8085). This script:
    1. LOOKS FIRST for a LibreHardwareMonitor you already have (running, set to start with Windows, or
       saved in the usual places: Program Files, Downloads, Desktop, Documents, C:\Tools, this folder...)
       and shows what it found. Nothing is changed until you say yes.
    2. Uses the copy it found. Only if there is none does it download the latest release from
       github.com/LibreHardwareMonitor into Program Files\LibreHardwareMonitor.
    3. Turns on its web server (port 8085), starts it minimized to the tray, and closing its window keeps
       it running.
    4. Starts it with Windows, as administrator (needed to read the CPU sensors), without a prompt:
       Task Scheduler task "LibreHardwareMonitor (NZB-CC)". Skipped if it already starts with Windows.
    5. Starts it and checks that HAL can read a CPU temperature.
  The web server is for this PC: no firewall port is opened, so other devices can't reach it.
  Safe to run again. Undo: run with -Undo (removes the startup task; LibreHardwareMonitor's files stay).
#>
param([switch]$Elevated, [string]$ForUser, [string]$ForProfile, [switch]$Undo)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    if ($Elevated) { Write-Host "Administrator rights were not granted." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
    $a = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated -ForUser `"$env:USERDOMAIN\$env:USERNAME`" -ForProfile `"$env:USERPROFILE`""
    if ($Undo) { $a += " -Undo" }
    Start-Process powershell.exe -Verb RunAs -ArgumentList $a
    exit
}

& {
$ErrorActionPreference = "Stop"
$TaskName = "LibreHardwareMonitor (NZB-CC)"
$Port = 8085
$user = if ($ForUser) { $ForUser } else { "$env:USERDOMAIN\$env:USERNAME" }
$profileDir = if ($ForProfile) { $ForProfile } else { $env:USERPROFILE }
function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
function Ask($q) { (Read-Host "$q (Y/N)").Trim().ToUpper() -eq "Y" }

function ReadSensors {
    try { return Invoke-RestMethod "http://localhost:$Port/data.json" -TimeoutSec 3 } catch { return $null }
}
# The CPU temperatures in LibreHardwareMonitor's tree (names like "CPU Package" or "Core (Tctl/Tdie)").
function CpuTemps($node, [bool]$underCpu = $false) {
    $out = @()
    if (-not $node) { return $out }
    $isCpu = $underCpu -or ("$($node.ImageURL)" -match 'cpu')
    # (some motherboards list it under their own chip as "CPU Core" / "CPU", as HAL also accepts)
    if (($isCpu -or "$($node.Text)" -match '^CPU') -and "$($node.Value)" -match ([string][char]0xB0 + 'C') -and "$($node.Text)" -notmatch 'distance') { $out += "$($node.Text) $($node.Value)" }
    foreach ($c in @($node.Children)) { $out += CpuTemps $c $isCpu }
    return $out
}
function StartupEntries {
    $found = @()
    foreach ($t in @(Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        foreach ($act in @($t.Actions)) { if ("$($act.Execute)" -match 'LibreHardwareMonitor') { $found += "Task Scheduler: $($t.TaskName)"; break } }
    }
    foreach ($k in "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run", "HKLM:\Software\Microsoft\Windows\CurrentVersion\Run") {
        $p = Get-ItemProperty $k -ErrorAction SilentlyContinue
        if ($p) { foreach ($n in $p.PSObject.Properties) { if ("$($n.Value)" -match 'LibreHardwareMonitor\.exe') { $found += "Startup entry: $($n.Name)" } } }
    }
    return $found
}

try {
    Say "CPU temperatures - LibreHardwareMonitor" Cyan
    Say "`"I am putting myself to the fullest possible use.`"`n" DarkGray

    if ($Undo) {
        if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
            Say "Removed the startup task `"$TaskName`"." Green
        } else { Say "There was no startup task `"$TaskName`" to remove." }
        Say "LibreHardwareMonitor itself was left as it is (close it from its tray icon if you like)."
        return
    }

    # ---------- 1. Look first ----------
    Say "Looking for LibreHardwareMonitor on this PC..." Cyan
    $running = @(Get-Process LibreHardwareMonitor -ErrorAction SilentlyContinue)
    $copies = @()
    foreach ($p in $running) { if ($p.Path) { $copies += $p.Path } }
    foreach ($t in @(Get-ScheduledTask -ErrorAction SilentlyContinue)) {
        foreach ($act in @($t.Actions)) { if ("$($act.Execute)" -match 'LibreHardwareMonitor\.exe') { $copies += ("$($act.Execute)".Trim('"')) } }
    }
    $places = @($PSScriptRoot, $env:ProgramFiles, ${env:ProgramFiles(x86)}, "C:\Tools", "C:\Program Files\LibreHardwareMonitor",
                (Join-Path $profileDir "Downloads"), (Join-Path $profileDir "Desktop"), (Join-Path $profileDir "Documents"),
                (Join-Path $profileDir "OneDrive"), (Join-Path $profileDir "AppData\Local\Microsoft\WinGet\Packages"),
                (Join-Path $profileDir "scoop\apps"), (Join-Path $profileDir "AppData\Local\Programs"))
    foreach ($pl in $places) {
        if ($pl -and (Test-Path $pl)) {
            $copies += @(Get-ChildItem -Path $pl -Filter LibreHardwareMonitor.exe -Recurse -Depth 4 -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName })
        }
    }
    # also a zip you downloaded but didn't unpack
    $zips = @()
    foreach ($pl in (Join-Path $profileDir "Downloads"), (Join-Path $profileDir "Desktop")) {
        if (Test-Path $pl) { $zips += @(Get-ChildItem -Path $pl -Filter "LibreHardwareMonitor*.zip" -File -ErrorAction SilentlyContinue | ForEach-Object { $_.FullName }) }
    }
    $copies = @($copies | Where-Object { $_ -and (Test-Path $_) } | ForEach-Object { (Resolve-Path $_).Path } | Sort-Object -Unique)
    $startup = @(StartupEntries)
    $data = ReadSensors
    $temps = @(CpuTemps $data)

    Say ""
    if ($copies) {
        Say "Found:" Green
        foreach ($c in $copies) {
            $v = (Get-Item $c).VersionInfo.FileVersion
            $isOn = [bool]($running | Where-Object { $_.Path -eq $c })
            Say ("  {0}  (version {1}){2}" -f $c, $v, $(if ($isOn) { "  - running now" } else { "" }))
        }
    } else { Say "No LibreHardwareMonitor program found." Yellow }
    if ($zips) { Say "Downloaded but not unpacked:" Yellow; foreach ($z in $zips) { Say "  $z" } }
    Say ("Starts with Windows: " + $(if ($startup) { $startup -join "; " } else { "no" }))
    Say ("Web server for HAL (port ${Port}): " + $(if ($data) { "on" } else { "off" }))
    if ($temps) { Say ("CPU temperature HAL can read: " + (($temps | Select-Object -First 2) -join ", ")) Green }

    if ($data -and $temps -and $startup) {
        Say "`nEverything is already set up: HAL can read the CPU temperature, and it starts with Windows. Nothing to change." Green
        return
    }

    # ---------- 2. Which copy ----------
    $exe = $null
    if ($copies) {
        $exe = @($copies | Where-Object { $running.Path -contains $_ })[0]
        if (-not $exe) { $exe = $copies[0] }
        if ($copies.Count -gt 1) { Say "`nMore than one copy: using $exe" }
        $plan = "use the copy at $exe"
    } elseif ($zips) {
        $plan = "unpack $($zips[0]) into $env:ProgramFiles\LibreHardwareMonitor"
    } else {
        $plan = "download the latest LibreHardwareMonitor from GitHub into $env:ProgramFiles\LibreHardwareMonitor"
    }
    Say "`nWhat this will do:" Cyan
    Say "  - $plan"
    Say "  - turn on its web server (port $Port), start it minimized to the tray, keep it running when its window is closed"
    if (-not $startup) { Say "  - start it with Windows as administrator (task `"$TaskName`")" }
    Say "  - restart it and check HAL can read the CPU temperature"
    if (-not (Ask "`nGo ahead?")) { Say "Nothing was changed."; return }

    if (-not $exe) {
        $dest = Join-Path $env:ProgramFiles "LibreHardwareMonitor"
        New-Item -ItemType Directory -Path $dest -Force | Out-Null
        $zip = $null
        if ($zips) { $zip = $zips[0] } else {
            Say "Downloading..."
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $rel = Invoke-RestMethod "https://api.github.com/repos/LibreHardwareMonitor/LibreHardwareMonitor/releases/latest" -Headers @{ "User-Agent" = "nzbcc-setup" }
            $assets = @($rel.assets | Where-Object { $_.name -match '\.zip$' -and $_.name -notmatch 'LibreHardwareMonitorLib' })
            # the .NET Framework build runs on any Windows 10/11 without installing anything
            $asset = @($assets | Where-Object { $_.name -match 'net4' })[0]
            if (-not $asset) { $asset = $assets[0] }
            if (-not $asset) { throw "Couldn't find the download in LibreHardwareMonitor's latest release ($($rel.tag_name))." }
            $zip = Join-Path $env:TEMP $asset.name
            Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
            Say "Got $($asset.name) ($($rel.tag_name))."
        }
        Unblock-File $zip -ErrorAction SilentlyContinue
        Expand-Archive -Path $zip -DestinationPath $dest -Force
        $exe = @(Get-ChildItem $dest -Filter LibreHardwareMonitor.exe -Recurse -File | Select-Object -First 1 -ExpandProperty FullName)[0]
        if (-not $exe) { throw "LibreHardwareMonitor.exe wasn't in the download." }
        Get-ChildItem (Split-Path $exe) -Recurse -File | Unblock-File -ErrorAction SilentlyContinue
        Say "Installed in $(Split-Path $exe)." Green
    }

    # ---------- 3. Its settings (it saves them when it closes, so close it first) ----------
    foreach ($p in @(Get-Process LibreHardwareMonitor -ErrorAction SilentlyContinue)) {
        try { $null = $p.CloseMainWindow(); $null = $p.WaitForExit(5000) } catch { }
        if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue }
    }
    Start-Sleep -Milliseconds 500
    $cfgPath = Join-Path (Split-Path $exe) "LibreHardwareMonitor.config"
    [xml]$cfg = if (Test-Path $cfgPath) { Get-Content $cfgPath -Raw } else { '<?xml version="1.0" encoding="utf-8"?><configuration><appSettings /></configuration>' }
    $app = $cfg.SelectSingleNode("/configuration/appSettings")
    if (-not $app) { $app = $cfg.DocumentElement.AppendChild($cfg.CreateElement("appSettings")) }
    $want = [ordered]@{ "runWebServerMenuItem" = "true"; "listenerPort" = "$Port"; "startMinMenuItem" = "true"; "minTrayMenuItem" = "true"; "minCloseMenuItem" = "true" }
    foreach ($k in $want.Keys) {
        $n = $app.SelectSingleNode("add[@key='$k']")
        if (-not $n) { $n = $app.AppendChild($cfg.CreateElement("add")); $null = $n.SetAttribute("key", $k) }
        $null = $n.SetAttribute("value", $want[$k])
    }
    $cfg.Save($cfgPath)
    Say "Web server on (port $Port), starts minimized to the tray." Green

    # ---------- 4. Start with Windows ----------
    if (-not $startup) {
        $action = New-ScheduledTaskAction -Execute $exe -WorkingDirectory (Split-Path $exe)
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
                        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
        $trig = New-ScheduledTaskTrigger -AtLogOn -User $user
        $pr = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trig -Principal $pr -Settings $settings `
            -Description "NZB-CC: LibreHardwareMonitor for HAL's CPU temperatures (web server on port $Port)." -Force | Out-Null
        Say "Starts with Windows (task `"$TaskName`")." Green
    }

    # ---------- 5. Start it and check ----------
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Start-ScheduledTask -TaskName $TaskName }
    else { Start-Process $exe -WorkingDirectory (Split-Path $exe) }
    Say "Starting it and waiting for the first readings..."
    $temps = @()
    for ($i = 0; $i -lt 20 -and -not $temps; $i++) { Start-Sleep 2; $temps = @(CpuTemps (ReadSensors)) }
    if ($temps) {
        Say ("`nDone. HAL can read the CPU temperature: " + (($temps | Select-Object -First 2) -join ", ")) Green
        Say "It shows on the phone and the TV within a few seconds." Green
    } elseif (ReadSensors) {
        Say "`nThe web server is on, but it shows no CPU temperature yet." Yellow
        Say "Open LibreHardwareMonitor from its tray icon: if your CPU has no temperatures listed, Windows Security may have" Yellow
        Say "blocked its sensor driver (Protection history). Updating LibreHardwareMonitor usually fixes that." Yellow
    } else {
        Say "`nLibreHardwareMonitor didn't answer on port $Port." Yellow
        Say "Open it from its tray icon and check Options > Remote Web Server > Run is ticked, then run this again." Yellow
    }
} catch {
    Say "`nSomething went wrong: $($_.Exception.Message)" Red
}
}
Read-Host "`nPress Enter to close"
