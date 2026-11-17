<#
  NZB Command Center - start things with Windows.

  Keep this file in your NZB Command Center folder and right-click it > Run with PowerShell.
  It asks for administrator rights (one Windows prompt), then:
    1. Phone server: creates the Task Scheduler task "NZB Mobile Server" that starts
       mobile_server.py hidden, AS ADMINISTRATOR, every time you log in to Windows (no prompt
       at logon). Administrator lets it read drive temperatures and health counters.
       The old Startup-folder launcher is removed so it doesn't start twice.
    2. Jellyfin: checks it is set to start with Windows (service or tray app) and fixes it if not.
    3. Checks both answer, and prints a summary.
  Safe to run again. To undo the phone part: Task Scheduler > delete "NZB Mobile Server".
#>
param([switch]$Elevated, [string]$ForUser)

# --- Ask for administrator rights (re-runs this script elevated) ---
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    if ($Elevated) { Write-Host "Administrator rights were not granted." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
    # Pass on who is running this: if the prompt is answered with a different (administrator)
    # account, the task must still be for this user, not for that account.
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated -ForUser `"$env:USERDOMAIN\$env:USERNAME`""
    exit
}

& {
$ErrorActionPreference = "Stop"
$TaskName = "NZB Mobile Server"
function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
# Programs are run with 'Continue': under 'Stop', Windows PowerShell 5.1 turns any line a program writes
# to stderr into a fatal error before its exit code can be checked.
function Works($p) { if (-not $p) { return $false }; $ErrorActionPreference = 'Continue'; try { $o = & $p -c "print(42)" 2>$null; return ("$o".Trim() -eq "42") } catch { return $false } }
function Up($url) { try { $null = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 5; return $true } catch { return [bool]$_.Exception.Response } }
$summary = @()
try {
    Say "NZB Command Center - start with Windows" Cyan

    # ================= 1. Phone server as administrator at logon =================
    $Folder = $PSScriptRoot
    if (-not (Test-Path (Join-Path $Folder "nzb_manager_config.json"))) { $Folder = (Read-Host "Path to your NZB Command Center folder").Trim('"', ' ') }
    $Script = Join-Path $Folder "mobile_server.py"
    if (-not (Test-Path $Script)) { throw "No mobile_server.py in $Folder." }
    Say "`nPhone server ($Folder)" Cyan

    # Options it currently runs with (e.g. --port 9000), and the running copies to stop.
    $running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' OR Name = 'py.exe'" | Where-Object { $_.CommandLine -match 'mobile_server\.py' })
    $extra = ""
    if ($running.Count -and $running[0].CommandLine -match 'mobile_server\.py"?\s*(.*)$') { $extra = $Matches[1].Trim() }
    $port = if ($extra -match '--port\s+(\d+)') { $Matches[1] } elseif ($env:NZB_MOBILE_PORT) { $env:NZB_MOBILE_PORT } else { "8765" }
    $probeHost = if ($extra -match '--host\s+(\S+)' -and $Matches[1] -notin @('0.0.0.0', '::')) { $Matches[1] } else { "127.0.0.1" }

    # A windowless Python that actually runs (the Microsoft Store one only starts by its command name).
    $python = $null
    $cands = @("py", "python", "python3")
    foreach ($r in $running) { if ($r.ExecutablePath) { $cands += (Join-Path (Split-Path $r.ExecutablePath) "python.exe") } }
    foreach ($c in $cands) { $cmd = Get-Command $c -ErrorAction SilentlyContinue; $p = if ($cmd) { $cmd.Source } else { $null }; if (Works $p) { $python = $p; break } }
    if (-not $python) { throw "Couldn't find a Python that runs. Install Python, then run this again." }
    # Its windowless twin: pythonw.exe next to python.exe, or pyw.exe for the py launcher.
    $twin = if ((Split-Path $python -Leaf) -eq "py.exe") { "pyw" } else { "pythonw" }
    $pyw = Join-Path (Split-Path $python) "$twin.exe"
    if (-not (Test-Path $pyw)) { $wc = Get-Command $twin -ErrorAction SilentlyContinue; $pyw = if ($wc) { $wc.Source } else { $python } }
    Say "Python: $pyw"

    # The task: at logon of this user, highest privileges, hidden, restarted if it crashes.
    $user = if ($ForUser) { $ForUser } else { "$env:USERDOMAIN\$env:USERNAME" }
    if ($user -ne "$env:USERDOMAIN\$env:USERNAME") { Say "Setting this up for $user (the administrator prompt was answered with another account)." Yellow }
    $argLine = ("`"$Script`" " + $extra).Trim()
    $action = New-ScheduledTaskAction -Execute $pyw -Argument $argLine -WorkingDirectory $Folder
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $trigger.Delay = "PT20S"   # give the network a moment after logon
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings `
        -Description "Starts NZB Command Center's phone server (mobile_server.py) as administrator when $env:USERNAME logs in." -Force | Out-Null
    Say "Task '$TaskName' created: starts at logon, as administrator, hidden." Green

    # Only now that the task exists: stop what's running and remove the old Startup-folder launcher.
    if ($running.Count) { $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep 2; Say "Stopped the running phone server." }
    $vbs = Join-Path ([Environment]::GetFolderPath("Startup")) "NZB Mobile Server.vbs"
    if (Test-Path $vbs) { Remove-Item $vbs -Force; Say "Removed the old Startup-folder launcher." }
    elseif ($ForUser -and $user -ne "$env:USERDOMAIN\$env:USERNAME") { Say "If $user has 'NZB Mobile Server.vbs' in their Startup folder, delete it so it doesn't start twice." Yellow }

    Start-ScheduledTask -TaskName $TaskName
    $ok = $false; foreach ($i in 1..15) { Start-Sleep 1; if (Up "http://${probeHost}:$port/") { $ok = $true; break } }
    if ($ok) { Say "Phone server is running (port $port), as administrator." Green; $summary += "Phone server: starts at logon as administrator - running now" }
    else { Say "The task started, but the phone server isn't answering on port $port yet. Check mobile_server.log in $Folder." Yellow; $summary += "Phone server: task created, but not answering yet - see mobile_server.log" }

    # ================= 2. Jellyfin starts with Windows =================
    Say "`nJellyfin" Cyan
    $svc = Get-Service | Where-Object { $_.Name -match 'jellyfin' -or $_.DisplayName -match 'jellyfin' } | Select-Object -First 1
    if ($svc) {
        Say "Installed as a Windows service: $($svc.DisplayName) ($($svc.StartType), $($svc.Status))"
        if ($svc.StartType -ne 'Automatic') { Set-Service -Name $svc.Name -StartupType Automatic; Say "Set it to start automatically with Windows." Green }
        if ($svc.Status -ne 'Running') { Start-Service -Name $svc.Name; Say "Started it." Green }
        $summary += "Jellyfin: Windows service, starts automatically (even before anyone logs in)"
    } else {
        # Tray-app install: Jellyfin runs while you're logged in, started by its tray app.
        $exe = @("$env:ProgramFiles\Jellyfin\Server\Jellyfin.Windows.Tray.exe", "${env:ProgramFiles(x86)}\Jellyfin\Server\Jellyfin.Windows.Tray.exe",
                 "$env:ProgramFiles\Jellyfin\Server\jellyfin.exe", "$env:LOCALAPPDATA\Programs\Jellyfin\Server\jellyfin.exe") |
               Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
        if (-not $exe) {
            $proc = Get-Process -Name "Jellyfin*", "jellyfin" -ErrorAction SilentlyContinue | Where-Object { $_.Path } | Select-Object -First 1
            if ($proc) { $exe = $proc.Path }
        }
        if (-not $exe) {
            Say "Couldn't find Jellyfin on this PC (no service, no program in the usual places)." Yellow
            $summary += "Jellyfin: not found on this PC - is it installed somewhere else?"
        } else {
            Say "Installed as a program: $exe"
            # Startup entries that Task Manager > Startup apps has switched off still exist, but are
            # marked in StartupApproved (first byte odd = disabled): those don't count.
            $approvedBase = "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved"
            function Enabled($hive, $sub, $name) {
                $k = "${hive}:\$approvedBase\$sub"
                $v = (Get-ItemProperty $k -Name $name -ErrorAction SilentlyContinue).$name
                return (-not $v) -or (($v[0] % 2) -eq 0)
            }
            $runKeys = @(@("HKCU", "Software\Microsoft\Windows\CurrentVersion\Run", "Run"), @("HKLM", "Software\Microsoft\Windows\CurrentVersion\Run", "Run"),
                         @("HKLM", "Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run", "Run32"))
            $inRun = foreach ($rk in $runKeys) {
                $props = Get-ItemProperty "$($rk[0]):\$($rk[1])" -ErrorAction SilentlyContinue
                if ($props) { $props.PSObject.Properties | Where-Object { "$($_.Value)" -match 'jellyfin' -and (Enabled $rk[0] $rk[2] $_.Name) } }
            }
            $inStartup = @(@([Environment]::GetFolderPath("Startup"), "HKCU"), @([Environment]::GetFolderPath("CommonStartup"), "HKLM")) |
                         ForEach-Object { $hive = $_[1]; Get-ChildItem $_[0] -ErrorAction SilentlyContinue | Where-Object { $_.Name -match 'jellyfin' -and (Enabled $hive "StartupFolder" $_.Name) } }
            $inTask = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -match 'jellyfin' -and $_.State -ne 'Disabled' }
            if ($inRun -or $inStartup -or $inTask) {
                Say "It's already set to start when you log in." Green
                $summary += "Jellyfin: program, already starts at logon"
            } else {
                $jt = New-ScheduledTaskTrigger -AtLogOn -User $user
                $jp = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
                $js = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
                Register-ScheduledTask -TaskName "Jellyfin Server (at logon)" -Action (New-ScheduledTaskAction -Execute $exe -WorkingDirectory (Split-Path $exe)) `
                    -Trigger $jt -Principal $jp -Settings $js -Description "Starts Jellyfin when $env:USERNAME logs in." -Force | Out-Null
                Say "It wasn't set to start with Windows: added task 'Jellyfin Server (at logon)'." Green
                $summary += "Jellyfin: program - now starts at logon (task 'Jellyfin Server (at logon)')"
            }
            if (-not (Get-Process -Name "Jellyfin*", "jellyfin" -ErrorAction SilentlyContinue)) {
                # Start it as the normal user, not from this administrator window: a media server
                # shouldn't run with admin rights (explorer.exe always opens things un-elevated).
                Start-Process explorer.exe -ArgumentList "`"$exe`""
                Say "Started Jellyfin." Green
            }
        }
    }
    $jfUp = $false; foreach ($i in 1..20) { if (Up "http://127.0.0.1:8096/System/Info/Public") { $jfUp = $true; break }; Start-Sleep 1 }
    if ($jfUp) { Say "Jellyfin is answering on port 8096." Green } else { Say "Jellyfin isn't answering on port 8096 yet (it can take a minute to start)." Yellow }
} catch {
    Say "`n$($_.Exception.Message)" Red
    $summary += "Stopped early: $($_.Exception.Message)"
}
Say "`n==== Summary ====" Cyan
$summary | ForEach-Object { Say " - $_" }
}
Read-Host "`nPress Enter to close"
