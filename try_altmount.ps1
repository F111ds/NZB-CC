<#
  try_altmount.ps1 - try streaming straight from Usenet with AltMount, without touching SABnzbd,
  Sonarr or Radarr.

  What it does:
    1. Downloads the latest AltMount for Windows from GitHub into F:\AltMount (first run only).
    2. Starts it on port 8090 (SABnzbd already uses 8080) in its own window.
    3. Opens AltMount's web page once it answers, and prints the settings Kodi on the TV needs.

  Stop it: close the AltMount window. Remove it: delete F:\AltMount.
  Guide: docs/altmount-streaming-test.md

  Usage (in PowerShell, from the NZB-CC folder):
    powershell -ExecutionPolicy Bypass -File .\try_altmount.ps1
    powershell -ExecutionPolicy Bypass -File .\try_altmount.ps1 -Update    # fetch a newer AltMount first
#>
param(
    [string]$Folder = "F:\AltMount",
    [int]$Port = 8090,
    [switch]$Update
)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

$drive = Split-Path -Path $Folder -Qualifier -ErrorAction SilentlyContinue
if ($drive -and -not (Test-Path "$drive\")) {
    Say "Drive $drive isn't there. Plug it in, or choose another folder, e.g. -Folder D:\AltMount" Red
    exit 1
}
New-Item -ItemType Directory -Force -Path $Folder | Out-Null
$exe = Get-ChildItem -Path $Folder -Filter "altmount*windows*.exe" -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1

# 1. Download (first run, or -Update)
if (-not $exe -or $Update) {
    Say "Looking up the latest AltMount release on GitHub..." Cyan
    try {
        $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/kipsilabs/altmount/releases/latest" `
                                 -Headers @{ "User-Agent" = "try-altmount" }
    } catch {
        Say "Couldn't reach GitHub: $($_.Exception.Message)" Red
        Say "Download it by hand instead: https://github.com/kipsilabs/altmount/releases" Yellow
        Say "  (the file ending in _windows_amd64.zip), and unzip it into $Folder." Yellow
        exit 1
    }
    $asset = $rel.assets | Where-Object { $_.name -match "windows_amd64\.zip$" } | Select-Object -First 1
    if (-not $asset) {
        Say "Release $($rel.tag_name) has no Windows download." Red
        Say "Pick one by hand: https://github.com/kipsilabs/altmount/releases" Yellow
        exit 1
    }
    $zip = Join-Path $Folder $asset.name
    Say "Downloading $($asset.name) ($([math]::Round($asset.size / 1MB)) MB)..." Cyan
    Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $zip -UseBasicParsing
    Expand-Archive -Path $zip -DestinationPath $Folder -Force
    Remove-Item $zip
    $exe = Get-ChildItem -Path $Folder -Filter "altmount*windows*.exe" |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $exe) { Say "The download didn't contain an .exe. Check $Folder." Red; exit 1 }
    Unblock-File -Path $exe.FullName
    Say "Installed $($rel.tag_name) in $Folder" Green
}

# 2. Already running, or port taken?
$base = "http://localhost:$Port"
$inUse = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($inUse) {
    $owner = (Get-Process -Id ($inUse | Select-Object -First 1).OwningProcess -ErrorAction SilentlyContinue).ProcessName
    if ($owner -like "altmount*") {
        Say "AltMount is already running on port $Port." Green
        Start-Process $base
        exit 0
    }
    Say "Port $Port is already used by '$owner'. Run again with another port, e.g. -Port 8091" Red
    exit 1
}

# 3. Start it in its own window (its log scrolls there; closing the window stops it)
Say "Starting AltMount on port $Port..." Cyan
$env:PORT = "$Port"
# AltMount signs its logins with a secret it won't start without. Made once and kept in the folder, so you stay
# signed in across restarts.
$secretFile = Join-Path $Folder "jwt_secret.txt"
if (-not (Test-Path $secretFile)) {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    [IO.File]::WriteAllText($secretFile, [Convert]::ToBase64String($bytes))
}
$env:JWT_SECRET = (Get-Content $secretFile -Raw).Trim()

# AltMount 0.4.0 refuses to start (a CORS "insecure setup" panic) unless its settings list the web addresses its
# page is opened from. It writes config.yaml on its first start, so let it do that once, then add the line.
$configFile = Join-Path $Folder "config.yaml"
function Set-AllowedOrigins {
    $c = @(Get-Content $configFile)
    $line = "allowed_origins: ['http://localhost:$Port', 'http://127.0.0.1:$Port']"
    if ($c -match '^\s*allowed_origins:') {
        $c = $c -replace '^(\s*)allowed_origins:.*$', ('$1' + $line)
    } else {
        $i = [array]::IndexOf($c, 'api:')
        if ($i -lt 0) { Say "Couldn't find the api: section in $configFile." Red; exit 1 }
        $indent = if ($i + 1 -lt $c.Count) { $c[$i + 1] -replace '^(\s*).*', '$1' } else { '    ' }
        if (-not $indent) { $indent = '    ' }
        $rest = if ($i + 1 -lt $c.Count) { $c[($i + 1)..($c.Count - 1)] } else { @() }
        $c = $c[0..$i] + ($indent + $line) + $rest
    }
    Set-Content -Path $configFile -Value $c
}
if (-not (Test-Path $configFile)) {
    Say "First start: letting AltMount write its settings..." Cyan
    $first = Start-Process -FilePath $exe.FullName -ArgumentList "serve" -WorkingDirectory $Folder -PassThru -WindowStyle Hidden
    for ($i = 0; $i -lt 20 -and -not (Test-Path $configFile); $i++) { Start-Sleep -Seconds 1 }
    Start-Sleep -Seconds 2
    if (-not $first.HasExited) { Stop-Process -Id $first.Id -Force -ErrorAction SilentlyContinue }
    if (-not (Test-Path $configFile)) { Say "AltMount didn't write $configFile. Look at altmount.log in $Folder." Red; exit 1 }
}
Set-AllowedOrigins

$proc = Start-Process -FilePath $exe.FullName -ArgumentList "serve" -WorkingDirectory $Folder -PassThru

$up = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 1
    if ($proc.HasExited) {
        Say "AltMount stopped straight away. The end of its log:" Red
        Get-Content (Join-Path $Folder "altmount.log") -Tail 15 -ErrorAction SilentlyContinue | ForEach-Object { Say "  $_" }
        exit 1
    }
    try {
        $r = Invoke-WebRequest -Uri "$base/live" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $up = $true; break }
    } catch { }
}
if (-not $up) {
    Say "AltMount didn't answer within a minute. Look at its window for the error." Red
    exit 1
}

Say ""
Say "AltMount is running." Green
Say "  Web page:  $base   (opening it now)"
Say ""
Say "If Windows asks whether to allow AltMount on the network, tick PRIVATE networks and Allow,"
Say "otherwise the TV can't reach it."
Say ""
Say "Kodi on the TV - Videos > Files > Add videos > Browse > Add network location:" Cyan
Say "  Protocol:     WebDAV server (HTTP)"
Say "  Server:       $env:COMPUTERNAME"
Say "  Remote path:  webdav"
Say "  Port:         $Port"
Say "  Username / password: the WebDAV ones in AltMount's Configuration (default usenet / usenet)"
Say ""
Say "To stop AltMount, close its window."
Start-Process $base
