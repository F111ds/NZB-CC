<#
  Sonarr + Radarr + Prowlarr - finish an install that stopped part way.

  Picks up where ArrSuite-Installer.ps1 stopped. That installer failed because each download
  unpacks into its own subfolder (bin\Sonarr\Sonarr.exe), not straight into bin\, so it
  couldn't find Sonarr.exe. This script:
    1. reuses what is already there: an unpacked app, or a downloaded zip in _install_cache.
       It only downloads what's missing.
    2. moves the files up out of that subfolder, so each app ends up in <App>\bin.
    3. keeps each app's data (settings, database, logs) in <App>\data on the same drive.
    4. adds a startup shortcut for each one, so it starts when you sign in to Windows.
    5. starts each app and checks its web page answers.
  It never touches SABnzbd, Jellyfin, Kodi or your media folders, and never deletes a data folder.
  Run it as often as you like: an app that's already installed and running is left alone.
#>
param([string]$Root = 'Z:\MediaAutomation')

& {
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.IO.Compression.FileSystem
function Say($t, $c = 'Gray') { Write-Host $t -ForegroundColor $c }

$Apps = @(
    @{ Name = 'Sonarr';   Port = 8989; Url = 'https://services.sonarr.tv/v1/download/main/latest?version=4&os=windows&arch=x64' },
    @{ Name = 'Radarr';   Port = 7878; Url = 'https://radarr.servarr.com/v1/update/master/updatefile?os=windows&runtime=netcore&arch=x64' },
    @{ Name = 'Prowlarr'; Port = 9696; Url = 'https://prowlarr.servarr.com/v1/update/master/updatefile?os=windows&runtime=netcore&arch=x64' }
)

function ZipOk($path) {
    if (-not (Test-Path $path) -or (Get-Item $path).Length -lt 1MB) { return $false }
    try { $z = [IO.Compression.ZipFile]::OpenRead($path); $n = $z.Entries.Count; $z.Dispose(); return ($n -ge 5) } catch { return $false }
}

function FindExe($dir, $exeName) {
    if (-not (Test-Path $dir)) { return $null }
    Get-ChildItem -LiteralPath $dir -Recurse -Filter $exeName -File -ErrorAction SilentlyContinue |
        Sort-Object { $_.FullName.Length } | Select-Object -First 1
}

function Listening($port) {
    try { return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop) } catch { return $false }
}

try {
    Say "Sonarr + Radarr + Prowlarr: finishing the install in $Root" Cyan
    if (-not (Test-Path ([IO.Path]::GetPathRoot($Root)))) { throw "Drive for $Root doesn't exist." }
    $cache = Join-Path $Root '_install_cache'
    New-Item -ItemType Directory -Force -Path $Root, $cache | Out-Null
    $startup = [Environment]::GetFolderPath('Startup')
    $shell = New-Object -ComObject WScript.Shell
    $done = @()

    foreach ($a in $Apps) {
        $name = $a.Name; $exeName = "$name.exe"
        $appRoot = Join-Path $Root $name
        $bin = Join-Path $appRoot 'bin'
        $data = Join-Path $appRoot 'data'
        $zip = Join-Path $cache "$name-windows-x64.zip"
        Say "`n--- $name ---" Cyan
        New-Item -ItemType Directory -Force -Path $bin, $data | Out-Null

        # 1. Already unpacked? Otherwise unpack from the saved zip, downloading it only if needed.
        $exe = FindExe $bin $exeName
        if ($exe) {
            Say "Already unpacked: $($exe.FullName)"
        } else {
            if (ZipOk $zip) {
                Say "Using the download already saved in _install_cache."
            } else {
                Say "Downloading $name..."
                Remove-Item -LiteralPath $zip -Force -ErrorAction SilentlyContinue
                Invoke-WebRequest -Uri $a.Url -OutFile $zip -UseBasicParsing
                if (-not (ZipOk $zip)) { throw "$name download is missing or not a valid zip." }
            }
            # Clear out a half-finished unpack (program files only; data is separate).
            Get-ChildItem -LiteralPath $bin -Force | Remove-Item -Recurse -Force
            Say "Unpacking..."
            [IO.Compression.ZipFile]::ExtractToDirectory($zip, $bin)
            $exe = FindExe $bin $exeName
            if (-not $exe) { throw "$name's download doesn't contain $exeName. Nothing else was changed for $name." }
        }

        # 2. Move everything up out of the subfolder (bin\Sonarr\* -> bin\*).
        $inner = $exe.DirectoryName
        if ($inner.TrimEnd('\') -ne $bin.TrimEnd('\')) {
            if (Get-Process -Name $name -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$bin*" }) {
                Say "$name is running from $inner; leaving its files where they are."
            } else {
                Say "Moving files from $inner up to $bin"
                Get-ChildItem -LiteralPath $inner -Force | Move-Item -Destination $bin -Force
                Remove-Item -LiteralPath $inner -Recurse -Force
                $exe = Get-Item (Join-Path $bin $exeName)
            }
        }
        $exePath = $exe.FullName

        # 3. Startup shortcut: runs the app directly, data kept in <App>\data.
        $old = Join-Path $appRoot "Start-$name.ps1"
        if (Test-Path $old) { Remove-Item $old -Force }
        $lnkArgs = "-nobrowser `"-data=$data`""
        $lnk = $shell.CreateShortcut((Join-Path $startup "Start $name.lnk"))
        $lnk.TargetPath = $exePath
        $lnk.Arguments = $lnkArgs
        $lnk.WorkingDirectory = $exe.DirectoryName
        $lnk.WindowStyle = 7
        $lnk.Description = "Start $name at Windows sign-in (data in $data)"
        $lnk.Save()
        Say "Startup shortcut: Start $name.lnk"

        # 4. Start it, unless it's already running.
        if (Get-Process -Name $name -ErrorAction SilentlyContinue) {
            Say "$name is already running; not starting a second copy."
        } elseif (Listening $a.Port) {
            Say "Something else is already using port $($a.Port). $name wasn't started; maybe another copy is installed elsewhere." Yellow
        } else {
            Start-Process -FilePath $exePath -ArgumentList @('-nobrowser', "`"-data=$data`"") -WorkingDirectory $exe.DirectoryName -WindowStyle Minimized
            Say "Started $name."
        }
        $done += @{ Name = $name; Port = $a.Port; Bin = $exe.DirectoryName; Data = $data }
    }

    # 5. Check each web page answers (first start can take a minute).
    Say "`nWaiting for the web pages (up to 90 seconds)..." Cyan
    $deadline = (Get-Date).AddSeconds(90)
    $up = @{}
    while ((Get-Date) -lt $deadline -and $up.Count -lt $done.Count) {
        foreach ($d in $done) {
            if ($up[$d.Name]) { continue }
            try { $null = Invoke-WebRequest "http://localhost:$($d.Port)" -UseBasicParsing -TimeoutSec 3; $up[$d.Name] = $true }
            catch { if ($_.Exception.Response) { $up[$d.Name] = $true } }
        }
        if ($up.Count -lt $done.Count) { Start-Sleep 3 }
    }

    Say "`n====================  Results  ====================" Cyan
    foreach ($d in $done) {
        if ($up[$d.Name]) { Say ("{0,-9} http://localhost:{1}   OK" -f $d.Name, $d.Port) Green }
        else { Say ("{0,-9} http://localhost:{1}   not answering yet - give it a minute" -f $d.Name, $d.Port) Yellow }
        Say "          Program: $($d.Bin)"
        Say "          Data:    $($d.Data)"
    }
    Say "`nIf Windows asks about the firewall, allow Private networks."
    Say "Next: open each page, set up a login, then connect Prowlarr -> Sonarr/Radarr and SABnzbd -> Sonarr/Radarr."
    if ($up.Count -gt 0) {
        $o = Read-Host "`nOpen the web pages now? (Y/N)"
        if ($o -match '^(y|yes)$') { foreach ($d in $done) { Start-Process "http://localhost:$($d.Port)" } }
    }
} catch {
    Say "`nSTOPPED: $($_.Exception.Message)" Red
    Say "Nothing was deleted except a half-finished unpack. You can run this again."
}
}
Read-Host "`nPress Enter to close"
