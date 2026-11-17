<#
  Updates the NZB Command Center phone app (mobile_server.py) to the latest version on GitHub.

  Put this file in your NZB Command Center folder (next to mobile_server.py) and double-click
  update_phone_app.bat, or run:   powershell -ExecutionPolicy Bypass -File update_phone_app.ps1

  What it does:
    1. Downloads the newest mobile_server.py from GitHub and checks it's a real, working file.
    2. Stops the running phone server.
    3. Keeps your old copy as mobile_server.old.py.
    4. Puts the new file in place and starts the server again.
  Your settings, pairing code and saved searches live in other files and are not touched.
  If anything goes wrong before step 4, your old file stays as it was.
#>
param(
    [string]$Branch = "claude/laughing-feynman-495bmc"
)

$ErrorActionPreference = "Stop"
$Repo   = "F111ds/NZB-CC"
$Folder = $PSScriptRoot
$Target = Join-Path $Folder "mobile_server.py"
$Backup = Join-Path $Folder "mobile_server.old.py"
$Temp   = Join-Path $env:TEMP "mobile_server.new.py"

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }
function Fail($text) { Say "`n$text" Red; Read-Host "`nPress Enter to close"; exit 1 }

Say "NZB Command Center - phone app updater" Cyan
Say "Folder: $Folder`n"

if (-not (Test-Path (Join-Path $Folder "nzb_manager_config.json"))) {
    Fail "This folder has no nzb_manager_config.json. Put this script in your NZB Command Center folder and run it again."
}

# ---- 1. Download -----------------------------------------------------------------------------
$url = "https://raw.githubusercontent.com/$Repo/$Branch/mobile_server.py"
Say "Downloading the newest version..."
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $Temp -UseBasicParsing -Headers @{ "Cache-Control" = "no-cache" }
} catch {
    # A private repository can't be downloaded without signing in: fall back to a copy in Downloads.
    $dl = Join-Path ([Environment]::GetFolderPath("UserProfile")) "Downloads\mobile_server.py"
    if (Test-Path $dl) {
        Say "Couldn't download from GitHub ($($_.Exception.Message))." Yellow
        Say "Using the copy in your Downloads folder instead." Yellow
        Copy-Item $dl $Temp -Force
    } else {
        Fail ("Couldn't download from GitHub: $($_.Exception.Message)`n`n" +
              "If your repository is private, download mobile_server.py yourself (signed in to GitHub):`n" +
              "  https://github.com/$Repo/blob/$Branch/mobile_server.py  (click the download icon)`n" +
              "save it to your Downloads folder, and run this script again.")
    }
}

# ---- Check it ---------------------------------------------------------------------------------
$text = Get-Content $Temp -Raw -Encoding UTF8
if ($text -notmatch 'class MobileHandler') {
    Fail "The downloaded file isn't the phone app (maybe a GitHub sign-in page). Nothing was changed."
}
if ($text -notmatch 'VERSION = "([\d.]+)"') {
    Fail "The downloaded file has no version number, so it was not installed. Nothing was changed."
}
$newVersion = $Matches[1]
$oldVersion = "none"
if (Test-Path $Target) {
    $old = Get-Content $Target -Raw -Encoding UTF8
    if ($old -match 'VERSION = "([\d.]+)"') { $oldVersion = $Matches[1] }
}
Say "Installed version: $oldVersion"
Say "New version:       $newVersion" Green

# Is the server running? (Remember how, so it can be started the same way.)
$running = @(Get-CimInstance Win32_Process -Filter "Name LIKE 'python%' OR Name = 'py.exe'" |
    Where-Object { $_.CommandLine -match 'mobile_server\.py' })
$restartExe = $null; $restartArgs = ""
if ($running.Count -gt 0) {
    $restartExe = $running[0].ExecutablePath
    # Keep the same options it was started with (e.g. --port 9000), minus the script path itself.
    if ($running[0].CommandLine -match 'mobile_server\.py"?\s*(.*)$') { $restartArgs = $Matches[1].Trim() }
}

# Find Python: the one running the server first, then the py launcher, then python
# (skipping the Microsoft Store shortcut, which only opens the Store).
$python = $null
if ($restartExe) {
    $console = Join-Path (Split-Path $restartExe) "python.exe"
    $python = if (Test-Path $console) { $console } else { $restartExe }
}
if (-not $python) {
    foreach ($name in "py", "python") {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notmatch 'WindowsApps') { $python = $cmd.Source; break }
    }
}

if ($python) {
    $check = & $python -m py_compile $Temp 2>&1
    if ($LASTEXITCODE -ne 0) { Fail "The new file has an error, so nothing was changed:`n$check" }
}

# ---- 2. Stop the running server ---------------------------------------------------------------
if ($running.Count -gt 0) {
    Say "`nStopping the phone server (process $($running.ProcessId -join ', '))..."
    $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
} else {
    Say "`nThe phone server isn't running right now."
}

# ---- 3. Back up, 4. Replace ------------------------------------------------------------------
if (Test-Path $Target) {
    Copy-Item $Target $Backup -Force
    Say "Old version saved as mobile_server.old.py"
}
Move-Item $Temp $Target -Force
Say "Installed version $newVersion" Green

# ---- Start it again ---------------------------------------------------------------------------
$exe = if ($restartExe) { $restartExe } else { $python }
if (-not $exe) {
    Say "`nPython wasn't found, so start the server the way you usually do." Yellow
} else {
    $argList = "`"$Target`""
    if ($restartArgs) { $argList += " $restartArgs" }
    Start-Process -FilePath $exe -ArgumentList $argList -WorkingDirectory $Folder
    Say "`nPhone server started again." Green
}

Say "`nOn your phone: refresh the page (or close and reopen the home-screen app)." Cyan
Say "You should see a Jellyfin tab and a Drives card on Home."
Say "To go back to the old version: delete mobile_server.py and rename mobile_server.old.py to mobile_server.py."
Read-Host "`nPress Enter to close"
