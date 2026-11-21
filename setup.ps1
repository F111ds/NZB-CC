<#
  NZB Command Center - setup (one place for everything you set up once).

  Keep this file in your NZB Command Center folder and right-click it > Run with PowerShell.
  It shows what is set up on this PC and lets you run each step. Each step is its own script
  next to this one (they ask for administrator rights themselves when they need them):

    1. Phone server at Windows startup ....... setup_startup.ps1
    2. HAL 9000 inside the phone server ...... setup_hal_startup.ps1
    3. Jellyfin at Windows startup ........... setup_jellyfin_service.ps1
    4. Sign in automatically, then lock ...... setup_autologon.ps1
    5. Sonarr / Radarr / Prowlarr install .... install_arr_resume.ps1
    6. CPU temperatures for HAL .............. setup_lhm.ps1

  Run it as often as you like: every step leaves what is already right alone.
  Updating is update_all.ps1 (or Update everything in the app); backing up is backup_settings.ps1.
#>

& {
$Here = $PSScriptRoot
if (-not $Here) { $Here = (Get-Location).Path }

function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
function HasTask($n) { [bool](Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue) }
function Running($pattern) {
    [bool](Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' OR Name = 'py.exe'" -ErrorAction SilentlyContinue |
           Where-Object { $_.CommandLine -match $pattern })
}

$steps = @(
    @{ Name = "Phone server at Windows startup"; File = "setup_startup.ps1"; Undo = $false
       Check = { if (HasTask "NZB Mobile Server") { "set up" } elseif (Running 'mobile_server\.py') { "running, but not at startup" } else { "not set up" } } },
    @{ Name = "HAL 9000 inside the phone server"; File = "setup_hal_startup.ps1"; Undo = $false
       Check = {
           $embedded = (Test-Path (Join-Path $Here "hal_server.py")) -and
                       ((Get-Content (Join-Path $Here "mobile_server.py") -Raw -ErrorAction SilentlyContinue) -match 'start_embedded_hal')
           if (HasTask "HAL 9000 Server") { "separate task (run this to move it into the phone server)" }
           elseif ($embedded) { "set up" } else { "not set up" } } },
    @{ Name = "Jellyfin at Windows startup"; File = "setup_jellyfin_service.ps1"; Undo = $true
       Check = { if (HasTask "Jellyfin Server (at startup)") { "set up" } else { "not set up" } } },
    @{ Name = "Sign in automatically, then lock"; File = "setup_autologon.ps1"; Undo = $true
       Check = {
           $w = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction SilentlyContinue
           $on = $w -and "$($w.AutoAdminLogon)" -eq "1"
           $lock = HasTask "Lock screen after automatic login"
           if ($on -and $lock) { "set up" } elseif ($on) { "signs in, but the lock task is missing" } else { "not set up" } } },
    @{ Name = "Sonarr / Radarr / Prowlarr install"; File = "install_arr_resume.ps1"; Undo = $false
       Check = {
           $n = @(Get-Process Sonarr, Radarr, Prowlarr -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name -Unique).Count
           if ($n -eq 3) { "all three running" } elseif ($n) { "$n of 3 running" } else { "not running" } } },
    @{ Name = "CPU temperatures for HAL"; File = "setup_lhm.ps1"; Undo = $true
       Check = {
           $web = $false; try { $null = Invoke-WebRequest "http://localhost:8085/data.json" -UseBasicParsing -TimeoutSec 2; $web = $true } catch { }
           $auto = [bool](@(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { "$($_.Actions.Execute)" -match 'LibreHardwareMonitor' }).Count)
           if ($web -and $auto) { "set up" } elseif ($web) { "working, but not at startup" }
           elseif (Get-Process LibreHardwareMonitor -ErrorAction SilentlyContinue) { "running, but its web server is off" } else { "not set up" } } }
)

while ($true) {
    Clear-Host
    Say "NZB Command Center - setup" Cyan
    Say "Folder: $Here`n"
    for ($i = 0; $i -lt $steps.Count; $i++) {
        $s = $steps[$i]
        $state = "couldn't check"
        try { $state = & $s.Check } catch { }
        $color = if ($state -eq "set up" -or $state -eq "all three running") { "Green" } elseif ($state -like "not*") { "DarkGray" } else { "Yellow" }
        $missing = if (Test-Path (Join-Path $Here $s.File)) { "" } else { "  (script missing - run update_all.ps1)" }
        Write-Host ("  {0}. {1,-36}" -f ($i + 1), $s.Name) -NoNewline
        Say "$state$missing" $color
    }
    Say "`n  U. Undo a step (Jellyfin, automatic sign-in or CPU temperatures)"
    Say "  Q. Quit`n"
    $c = (Read-Host "Choose").Trim().ToUpper()
    if ($c -eq "Q" -or $c -eq "") { break }
    $undo = $false
    if ($c -eq "U") {
        $c = (Read-Host "Undo which step (3, 4 or 6)").Trim()
        $undo = $true
    }
    $n = 0
    if (-not [int]::TryParse($c, [ref]$n) -or $n -lt 1 -or $n -gt $steps.Count) { Say "Pick a number from the list." Yellow; Start-Sleep 2; continue }
    $s = $steps[$n - 1]
    if ($undo -and -not $s.Undo) { Say "That step has no undo." Yellow; Start-Sleep 2; continue }
    $file = Join-Path $Here $s.File
    if (-not (Test-Path $file)) { Say "$($s.File) isn't in this folder. Run update_all.ps1 first." Red; Read-Host "Press Enter"; continue }
    Say "`nRunning $($s.File)$(if ($undo) { ' -Undo' })..." Cyan
    $al = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$file`"")
    if ($undo) { $al += "-Undo" }
    # Runs in its own window and waits, so its own "Press Enter to close" and administrator prompt work as usual.
    Start-Process powershell.exe -ArgumentList $al -Wait
}
}
