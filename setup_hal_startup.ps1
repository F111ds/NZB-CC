<#
  HAL 9000 - start the HAL phone server when you log in to Windows.

  Keep this file in your NZB Command Center folder (next to hal_server.py) and right-click it >
  Run with PowerShell. It asks for administrator rights once, then:
    1. makes sure Python has psutil (pip install psutil),
    2. creates the Task Scheduler task "HAL 9000 Server": starts hal_server.py hidden, AS
       ADMINISTRATOR, when you log in, and again within a minute if it ever stops.
       Administrator lets HAL read drive temperatures, failed sign-ins and the security log, and
       restart services. It runs in your session, so Lock and Sleep from the phone work.
       No password is stored.
    3. opens Windows Firewall for HAL's port (8766) on private networks only,
    4. starts it and checks it answers.
  Safe to run again. To undo: Task Scheduler > delete "HAL 9000 Server".
#>
param([switch]$Elevated, [string]$ForUser, [int]$Port = 8766)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    if ($Elevated) { Write-Host "Administrator rights were not granted." -ForegroundColor Red; Read-Host "Press Enter to close"; exit 1 }
    $a = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated -ForUser `"$env:USERDOMAIN\$env:USERNAME`" -Port $Port"
    Start-Process powershell.exe -Verb RunAs -ArgumentList $a
    exit
}

& {
$ErrorActionPreference = "Stop"
$TaskName = "HAL 9000 Server"
function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
function Works($p) { if (-not $p) { return $false }; $ErrorActionPreference = 'Continue'; try { $o = & $p -c "print(42)" 2>$null; return ("$o".Trim() -eq "42") } catch { return $false } }
function Up($url) { try { $null = Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 5; return $true } catch { return [bool]$_.Exception.Response } }
try {
    Say "HAL 9000 - start with Windows" Cyan
    Say "`"Good afternoon, gentlemen. I am a HAL 9000 computer.`"`n" DarkGray
    $Folder = $PSScriptRoot
    $Script = Join-Path $Folder "hal_server.py"
    if (-not (Test-Path $Script)) { throw "No hal_server.py in $Folder. Put this script next to it." }
    if (-not (Test-Path (Join-Path $Folder "system_monitor.py"))) { throw "system_monitor.py is missing from $Folder (HAL needs it)." }

    # --- Python and psutil ---
    $running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' OR Name = 'py.exe'" | Where-Object { $_.CommandLine -match 'hal_server\.py' })
    $python = $null
    $cands = @("py", "python", "python3")
    foreach ($r in $running) { if ($r.ExecutablePath) { $cands += (Join-Path (Split-Path $r.ExecutablePath) "python.exe") } }
    foreach ($c in $cands) { $cmd = Get-Command $c -ErrorAction SilentlyContinue; $p = if ($cmd) { $cmd.Source } else { $null }; if (Works $p) { $python = $p; break } }
    if (-not $python) { throw "Couldn't find a Python that runs. Install Python, then run this again." }
    $ErrorActionPreference = 'Continue'
    & $python -c "import psutil" 2>$null
    if ($LASTEXITCODE -ne 0) {
        Say "Installing psutil..."
        & $python -m pip install --quiet psutil
        & $python -c "import psutil" 2>$null
        if ($LASTEXITCODE -ne 0) { $ErrorActionPreference = 'Stop'; throw "Couldn't install psutil. Run: $python -m pip install psutil" }
    }
    $ErrorActionPreference = 'Stop'
    Say "Python: $python (psutil OK)" Green
    $twin = if ((Split-Path $python -Leaf) -eq "py.exe") { "pyw" } else { "pythonw" }
    $pyw = Join-Path (Split-Path $python) "$twin.exe"
    if (-not (Test-Path $pyw)) { $wc = Get-Command $twin -ErrorAction SilentlyContinue; $pyw = if ($wc) { $wc.Source } else { $python } }

    # --- The task: at logon, hidden, highest privileges, restarted if it stops ---
    $user = if ($ForUser) { $ForUser } else { "$env:USERDOMAIN\$env:USERNAME" }
    $argLine = "`"$Script`"" + $(if ($Port -ne 8766) { " --port $Port" } else { "" })
    $action = New-ScheduledTaskAction -Execute $pyw -Argument $argLine -WorkingDirectory $Folder
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    $t = New-ScheduledTaskTrigger -AtLogOn -User $user
    $t.Delay = "PT20S"
    $pr = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $t -Principal $pr -Settings $settings `
        -Description "Starts HAL 9000 (hal_server.py) as administrator when $user logs in." -Force -ErrorAction Stop | Out-Null
    Say "Task '$TaskName' created: starts at logon, as administrator, hidden." Green

    # --- Firewall: private networks only (Tailscale counts as private) ---
    Get-NetFirewallRule -DisplayName "HAL 9000 ($Port)" -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName "HAL 9000 ($Port)" -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow -Profile Private, Domain | Out-Null
    Say "Firewall: port $Port open on private networks." Green

    # --- Switch over ---
    if ($running.Count) { $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep 2 }
    $vbs = Join-Path ([Environment]::GetFolderPath("Startup")) "HAL 9000 Server.vbs"
    if (Test-Path $vbs) { Remove-Item $vbs -Force; Say "Removed the old Startup-folder launcher." }
    Start-ScheduledTask -TaskName $TaskName
    $ok = $false; foreach ($i in 1..25) { Start-Sleep 1; if (Up "http://127.0.0.1:$Port/") { $ok = $true; break } }
    if (-not $ok) { throw "The task started, but HAL isn't answering on port $Port yet. Look at hal_server.log in $Folder." }

    $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' } | ForEach-Object { $_.IPAddress })
    $key = Join-Path $Folder "mobile_access_key.txt"
    Say "`nHAL 9000 is watching." Green
    Say "Open on your phone:  $(($ips | ForEach-Object { "http://${_}:$Port/" }) -join '  or  ')"
    if (Test-Path $key) { Say "Access code (the same as the NZB phone app):  $((Get-Content $key -Raw).Trim())" }
    Say "Then Share > Add to Home Screen (iPhone) or Install app (Android)." DarkGray
} catch {
    Say "`n$($_.Exception.Message)" Red
}
}
Read-Host "`nPress Enter to close"
