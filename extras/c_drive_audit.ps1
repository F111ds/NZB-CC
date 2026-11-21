<#
  C: drive deep-dive audit for HAL 9000.  READ-ONLY: it deletes, moves and changes nothing.

  Right-click > Run with PowerShell (it asks for administrator so it can see Windows' own folders).
  Takes 5-20 minutes. When done it writes  Desktop\HAL-C-Audit-<date>.txt  and opens it.
  Send that file to Claude for the analysis.
#>
param([switch]$Elevated)

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin -and -not $Elevated -and $PSCommandPath) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Elevated"
    exit
}

$ErrorActionPreference = "SilentlyContinue"
$ProgressPreference = "SilentlyContinue"
$stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
$out = Join-Path ([Environment]::GetFolderPath("Desktop")) "HAL-C-Audit-$stamp.txt"
$sb = New-Object System.Text.StringBuilder

function Add([string]$t = "") { [void]$sb.AppendLine($t) }
function Head([string]$t) { Add; Add ("=" * 100); Add "== $t"; Add ("=" * 100); Write-Host "  $t" -ForegroundColor Cyan }
function GB($b) { if ($null -eq $b) { return "      ?" }; "{0,8:N2} GB" -f ($b / 1GB) }
function Tbl($rows) { if ($rows) { Add (($rows | Format-Table -AutoSize -Wrap | Out-String -Width 400).TrimEnd()) } else { Add "  (none)" } }

# Fast folder sizer: skips junctions/symlinks (no double counting), survives access-denied,
# and remembers the biggest files it walks past.
Add-Type -TypeDefinition @"
using System; using System.IO; using System.Collections.Generic;
public static class Walk {
    public static List<KeyValuePair<string,long>> Big = new List<KeyValuePair<string,long>>();
    public static long MinBig = 500L * 1024 * 1024;
    public static long Size(string path, out long files, out DateTime newest) {
        files = 0; newest = DateTime.MinValue; long total = 0;
        var stack = new Stack<string>(); stack.Push(path);
        while (stack.Count > 0) {
            string d = stack.Pop();
            try {
                foreach (var f in new DirectoryInfo(d).EnumerateFileSystemInfos()) {
                    try {
                        if ((f.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                        if ((f.Attributes & FileAttributes.Directory) != 0) { stack.Push(f.FullName); continue; }
                        long len = ((FileInfo)f).Length; total += len; files++;
                        if (f.LastWriteTime > newest) newest = f.LastWriteTime;
                        if (len >= MinBig) Big.Add(new KeyValuePair<string,long>(f.FullName, len));
                    } catch {}
                }
            } catch {}
        }
        return total;
    }
}
"@

function ExePath([string]$cmd) {
    if (-not $cmd) { return $null }
    $c = [Environment]::ExpandEnvironmentVariables($cmd.Trim())
    if ($c -match '^"([^"]+)"') { return $Matches[1] }
    if ($c -match '^(.+?\.(exe|bat|cmd|ps1|py|vbs))\b') { return $Matches[1] }
    $c.Split(' ')[0]
}

$sizeCache = @{}
function DirInfo([string]$p) {
    if ($sizeCache.ContainsKey($p)) { return $sizeCache[$p] }
    $f = 0L; $n = [DateTime]::MinValue
    $s = if (Test-Path -LiteralPath $p) { [Walk]::Size($p, [ref]$f, [ref]$n) } else { $null }
    $r = [pscustomobject]@{ Path = $p; Bytes = $s; Size = (GB $s); Files = $f; NewestFile = $(if ($n -gt [DateTime]::MinValue) { $n.ToString("yyyy-MM-dd") } else { "" }) }
    $sizeCache[$p] = $r; $r
}
function Children([string]$p, [int]$top = 30) {
    if (-not (Test-Path -LiteralPath $p)) { return }
    Get-ChildItem -LiteralPath $p -Directory -Force | Where-Object { -not ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) } |
        ForEach-Object { DirInfo $_.FullName } | Sort-Object Bytes -Descending | Select-Object -First $top Path, Size, Files, NewestFile
}

Write-Host "HAL C: drive audit (read-only) - writing $out" -ForegroundColor Green
Add "HAL 9000 C: DRIVE AUDIT  -  $(Get-Date)  -  admin: $isAdmin"

# ---------------------------------------------------------------- system
Head "SYSTEM"
$os = Get-CimInstance Win32_OperatingSystem
$cs = Get-CimInstance Win32_ComputerSystem
$cv = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion"
Add "Computer : $($cs.Name)   $($cs.Manufacturer) $($cs.Model)   RAM $([math]::Round($cs.TotalPhysicalMemory/1GB,1)) GB"
Add "Windows  : $($os.Caption) $($cv.DisplayVersion) build $($os.BuildNumber).$($cv.UBR)"
Add "Installed: $($os.InstallDate)   Last boot: $($os.LastBootUpTime)"
Add "CPU      : $((Get-CimInstance Win32_Processor | Select-Object -First 1).Name)"
$lic = Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL AND Name LIKE 'Windows%'" | Select-Object -First 1
Add "Activation: $(switch ($lic.LicenseStatus) { 1 {'Licensed'} 0 {'Unlicensed'} default {"status $($lic.LicenseStatus)"} })"

# ---------------------------------------------------------------- drives
Head "DRIVES AND FREE SPACE (where things could move to)"
Tbl (Get-Volume | Where-Object DriveLetter | Sort-Object DriveLetter | Select-Object DriveLetter, FileSystemLabel, FileSystem, DriveType,
    @{n='Size';e={GB $_.Size}}, @{n='Free';e={GB $_.SizeRemaining}}, @{n='Free%';e={ if ($_.Size) { [math]::Round(100*$_.SizeRemaining/$_.Size) } }}, HealthStatus)
Add; Add "Physical disks:"
Tbl (Get-PhysicalDisk | Select-Object FriendlyName, MediaType, BusType, @{n='Size';e={GB $_.Size}}, HealthStatus, OperationalStatus)
Add; Add "Disk wear / errors:"
Tbl (Get-PhysicalDisk | ForEach-Object { $r = $_ | Get-StorageReliabilityCounter; [pscustomobject]@{ Disk=$_.FriendlyName; TempC=$r.Temperature; WearPct=$r.Wear; ReadErr=$r.ReadErrorsTotal; WriteErr=$r.WriteErrorsTotal; PowerOnHours=$r.PowerOnHours } })
Add; Add "Partitions on the C: disk:"
$cdisk = (Get-Partition -DriveLetter C).DiskNumber
Tbl (Get-Partition -DiskNumber $cdisk | Select-Object PartitionNumber, DriveLetter, Type, @{n='Size';e={GB $_.Size}})
Add; Add "TRIM: $((fsutil behavior query DisableDeleteNotify) -join ' | ')"

# ---------------------------------------------------------------- top level
Head "C:\ TOP-LEVEL FOLDERS"
Tbl (Children "C:\" 40)
Add; Add "Big files sitting directly in C:\ :"
Tbl (Get-ChildItem C:\ -File -Force | Sort-Object Length -Descending | Select-Object Name, @{n='Size';e={GB $_.Length}}, LastWriteTime)

foreach ($p in "C:\Program Files", "C:\Program Files (x86)", "C:\ProgramData") {
    Head "BIGGEST IN $p"
    Tbl (Children $p 40)
}

# ---------------------------------------------------------------- user profiles
Head "USER PROFILES"
$profiles = Get-CimInstance Win32_UserProfile | Where-Object { -not $_.Special }
Tbl ($profiles | ForEach-Object { $d = DirInfo $_.LocalPath; [pscustomobject]@{ Profile=$_.LocalPath; Size=$d.Size; LastUsed=$_.LastUseTime; Loaded=$_.Loaded } })
foreach ($pr in $profiles) {
    $u = $pr.LocalPath
    Head "INSIDE $u"
    Add "Main folders:"
    Tbl (Children $u 25)
    foreach ($sub in "AppData\Local", "AppData\Roaming", "AppData\LocalLow") {
        Add; Add "$sub (top 30):"
        Tbl (Children (Join-Path $u $sub) 30)
    }
    $dl = Join-Path $u "Downloads"
    if (Test-Path $dl) {
        Add; Add "Downloads - biggest 30 items:"
        Tbl (Get-ChildItem $dl -Force | ForEach-Object {
            $b = if ($_.PSIsContainer) { (DirInfo $_.FullName).Bytes } else { $_.Length }
            [pscustomobject]@{ Name=$_.Name; Bytes=$b; Size=(GB $b); Modified=$_.LastWriteTime.ToString("yyyy-MM-dd") } } |
            Sort-Object Bytes -Descending | Select-Object -First 30 Name, Size, Modified)
    }
}

# ---------------------------------------------------------------- installed programs
Head "INSTALLED PROGRAMS (registry)"
$keys = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"
$apps = Get-ItemProperty $keys | Where-Object { $_.DisplayName -and $_.SystemComponent -ne 1 -and -not $_.ParentKeyName } |
    ForEach-Object {
        $loc = $_.InstallLocation; if ($loc) { $loc = $loc.Trim('"').TrimEnd('\') }
        $uexe = ExePath $_.UninstallString
        $state = if ($loc -and -not (Test-Path -LiteralPath $loc)) { "FOLDER MISSING" }
                 elseif ($uexe -and $uexe -notmatch 'msiexec' -and $uexe -match '^[A-Za-z]:\\' -and -not (Test-Path -LiteralPath $uexe)) { "UNINSTALLER MISSING" } else { "" }
        [pscustomobject]@{
            Name = $_.DisplayName; Version = $_.DisplayVersion; Publisher = $_.Publisher
            Installed = $(if ($_.InstallDate -match '^\d{8}$') { "$($_.InstallDate.Substring(0,4))-$($_.InstallDate.Substring(4,2))-$($_.InstallDate.Substring(6,2))" } else { "" })
            SizeMB = $(if ($_.EstimatedSize) { [math]::Round($_.EstimatedSize/1024) } else { $null })
            Location = $loc; State = $state
        }
    } | Sort-Object Name -Unique
Add "$(@($apps).Count) programs."
Tbl ($apps | Sort-Object Name | Select-Object Name, Version, Publisher, Installed, SizeMB, State, Location)

Head "PROGRAMS WITH A BROKEN / LEFTOVER REGISTRY ENTRY"
Tbl ($apps | Where-Object State | Select-Object Name, Version, State, Location)

Head "RUNTIMES AND DUPLICATE VERSIONS (VC++, .NET, Java, Python, Node...)"
Tbl ($apps | Where-Object { $_.Name -match 'Visual C\+\+|\.NET|Java|JDK|JRE|Python|Node\.js|DirectX|Runtime|SDK' } | Sort-Object Name | Select-Object Name, Version, Installed, SizeMB)

Head "PROGRAM FOLDERS WITH NO INSTALLED PROGRAM (possible abandoned leftovers)"
$known = @($apps | ForEach-Object { $_.Location; $_.Name; $_.Publisher } | Where-Object { $_ })
$orphans = foreach ($root in "C:\Program Files", "C:\Program Files (x86)", "C:\ProgramData") {
    Get-ChildItem $root -Directory -Force | Where-Object {
        $n = $_.Name; $f = $_.FullName
        $n -notmatch '^(Microsoft|Windows|Common Files|Internet Explorer|ModifiableWindowsApps|WindowsApps|Reference Assemblies|MSBuild|dotnet|Package Cache|Packages|regid\.|USOPrivate|USOShared|SoftwareDistribution|ssh|Application Data|Desktop|Documents|Start Menu|Templates|Uninstall Information|Microsoft\.NET|WindowsPowerShell|Windows Defender|Windows Mail|Windows Media Player|Windows NT|Windows Photo Viewer|Windows Sidebar|Windows Security|Windows Kits|Comms|Intel|NVIDIA Corporation|AMD|Realtek)'
    } | Where-Object {
        $n = $_.Name; $f = $_.FullName
        -not ($known | Where-Object { $_ -like "$f*" -or $_ -like "*$n*" -or ($n.Length -ge 4 -and $n -like "*$_*" -and $_.Length -ge 4) } | Select-Object -First 1)
    } | ForEach-Object { DirInfo $_.FullName }
}
Tbl ($orphans | Sort-Object Bytes -Descending | Select-Object Path, Size, Files, NewestFile)

Head "MICROSOFT STORE APPS (non-system)"
Tbl (Get-AppxPackage -AllUsers | Where-Object { -not $_.IsFramework -and $_.SignatureKind -ne 'System' -and -not $_.NonRemovable } |
    Sort-Object Name | Select-Object Name, Version, @{n='Location';e={ if ($_.InstallLocation -notlike 'C:\Program Files\WindowsApps*') { $_.InstallLocation } }})
Add; Add "WindowsApps folder total: $((DirInfo 'C:\Program Files\WindowsApps').Size)"

# ---------------------------------------------------------------- Windows itself
Head "WINDOWS - RECLAIMABLE SPACE"
$winItems = [ordered]@{
    "Windows folder total"                     = "C:\Windows"
    "WinSxS component store (see DISM below)"  = "C:\Windows\WinSxS"
    "Windows Update downloads"                 = "C:\Windows\SoftwareDistribution\Download"
    "Delivery Optimization cache"              = "C:\Windows\ServiceProfiles\NetworkService\AppData\Local\Microsoft\Windows\DeliveryOptimization"
    "Windows Installer cache"                  = "C:\Windows\Installer"
    "Installer patch cache"                    = "C:\Windows\Installer\`$PatchCache`$"
    "Windows Temp"                             = "C:\Windows\Temp"
    "CBS / DISM logs"                          = "C:\Windows\Logs"
    "Crash dumps (Minidump)"                   = "C:\Windows\Minidump"
    "Live kernel reports"                      = "C:\Windows\LiveKernelReports"
    "Prefetch"                                 = "C:\Windows\Prefetch"
    "Driver store"                             = "C:\Windows\System32\DriverStore\FileRepository"
    "Error reports (WER)"                      = "C:\ProgramData\Microsoft\Windows\WER"
    "Windows Defender scans/history"           = "C:\ProgramData\Microsoft\Windows Defender\Scans"
    "Package Cache (installer copies)"         = "C:\ProgramData\Package Cache"
    "Old Windows install (Windows.old)"        = "C:\Windows.old"
    "Upgrade leftovers (`$Windows.~BT)"         = "C:\`$Windows.~BT"
    "Upgrade leftovers (`$Windows.~WS)"         = "C:\`$Windows.~WS"
    "ESD / upgrade files (`$WinREAgent)"        = "C:\`$WinREAgent"
    "Old system restore / SysReset"            = "C:\`$SysReset"
    "Windows10Upgrade"                         = "C:\Windows10Upgrade"
    "NVIDIA installer leftovers C:\NVIDIA"     = "C:\NVIDIA"
    "AMD installer leftovers C:\AMD"           = "C:\AMD"
    "Intel installer leftovers C:\Intel"       = "C:\Intel"
    "NVIDIA driver download cache"             = "C:\ProgramData\NVIDIA Corporation\Downloader"
    "Recycle Bin (all users)"                  = "C:\`$Recycle.Bin"
}
Add "Note: WinSxS and the Windows total look bigger than they are (hard links are counted more than once). The DISM section below gives the real figure."
Tbl ($winItems.GetEnumerator() | ForEach-Object { $d = DirInfo $_.Value; [pscustomobject]@{ Item=$_.Key; Size=$d.Size; Files=$d.Files; Path=$_.Value } })
$tempRows = foreach ($pr in $profiles) { $t = Join-Path $pr.LocalPath "AppData\Local\Temp"; $d = DirInfo $t; [pscustomobject]@{ Item="User Temp"; Size=$d.Size; Files=$d.Files; Path=$t } }
Tbl $tempRows
$dumpFile = "C:\Windows\MEMORY.DMP"
if (Test-Path $dumpFile) { Add "MEMORY.DMP: $(GB (Get-Item $dumpFile -Force).Length)  ($((Get-Item $dumpFile -Force).LastWriteTime))" }

Head "HIBERNATION, PAGE FILE, RESTORE POINTS"
foreach ($f in "C:\hiberfil.sys", "C:\pagefile.sys", "C:\swapfile.sys") { $i = Get-Item $f -Force; if ($i) { Add "$f : $(GB $i.Length)" } else { Add "$f : not present" } }
Add "Page file settings:"; Tbl (Get-CimInstance Win32_PageFileSetting | Select-Object Name, InitialSize, MaximumSize)
Add "Auto-managed page file: $($cs.AutomaticManagedPagefile)"
Add; Add "Hibernate / Fast startup:"; Add ((powercfg /a | Out-String).Trim())
Add; Add "Shadow storage (System Restore):"; Add ((vssadmin list shadowstorage | Out-String).Trim())
Add; Add "Restore points:"; Tbl (Get-ComputerRestorePoint | Select-Object SequenceNumber, @{n='Created';e={$_.ConvertToDateTime($_.CreationTime)}}, Description)
Add; Add "Reserved storage: $((DISM /Online /Get-ReservedStorageState | Select-String 'Reserved storage') -join ' ')"

Head "DISM COMPONENT STORE ANALYSIS (how much WinSxS can safely shrink)"
Add ((DISM /Online /Cleanup-Image /AnalyzeComponentStore | Out-String).Trim())
Add; Add "DISM health check:"; Add ((DISM /Online /Cleanup-Image /CheckHealth | Out-String).Trim())

Head "DRIVER STORE - OLD DUPLICATE DRIVERS"
$drv = pnputil /enum-drivers | Out-String
$recs = foreach ($blk in ($drv -split "(\r?\n){2,}")) {
    if ($blk -match 'Published Name:\s+(\S+)') {
        [pscustomobject]@{
            Inf = $Matches[1]
            Original = $(if ($blk -match 'Original Name:\s+(\S+)') { $Matches[1] })
            Provider = $(if ($blk -match 'Provider Name:\s+(.+)') { $Matches[1].Trim() })
            Class = $(if ($blk -match 'Class Name:\s+(.+)') { $Matches[1].Trim() })
            Version = $(if ($blk -match 'Driver Version:\s+(.+)') { $Matches[1].Trim() })
        }
    }
}
Add "$(@($recs).Count) third-party driver packages."
Tbl ($recs | Group-Object Original | Where-Object Count -gt 1 | ForEach-Object { $_.Group } | Sort-Object Original, Version | Select-Object Original, Inf, Provider, Class, Version)
Add; Add "Biggest driver folders:"; Tbl (Children "C:\Windows\System32\DriverStore\FileRepository" 15)

Head "WINDOWS UPDATE"
Tbl (Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 10 HotFixID, Description, InstalledOn)
$pend = @()
if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending") { $pend += "CBS" }
if (Test-Path "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired") { $pend += "WindowsUpdate" }
if ((Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager").PendingFileRenameOperations) { $pend += "PendingFileRename" }
Add "Reboot pending: $(if ($pend) { $pend -join ', ' } else { 'no' })"

# ---------------------------------------------------------------- dev / app caches
Head "DEVELOPER, GAME AND APP CACHES"
$caches = foreach ($pr in $profiles) {
    $u = $pr.LocalPath
    foreach ($rel in ".android", ".gradle", ".m2", ".nuget\packages", ".npm", ".cache", ".vscode\extensions", ".conda", "anaconda3", "miniconda3", ".docker", ".ollama",
                     "AppData\Local\Android\Sdk", "AppData\Local\pip\cache", "AppData\Local\npm-cache", "AppData\Roaming\npm-cache", "AppData\Local\Yarn",
                     "AppData\Local\NuGet", "AppData\Local\Docker", "AppData\Local\Packages", "AppData\Local\Programs", "AppData\Local\CrashDumps",
                     "AppData\Local\Microsoft\Windows\INetCache", "AppData\Local\Microsoft\Windows\Explorer", "AppData\Local\D3DSCache", "AppData\Local\NVIDIA\DXCache",
                     "AppData\Local\NVIDIA\GLCache", "AppData\Local\AMD\DxCache", "AppData\LocalLow\NVIDIA\PerDriverVersion\DXCache",
                     "AppData\Local\Google\Chrome\User Data", "AppData\Local\Microsoft\Edge\User Data", "AppData\Roaming\Mozilla\Firefox\Profiles",
                     "AppData\Local\Mozilla\Firefox\Profiles", "AppData\Roaming\Code\Cache", "AppData\Roaming\Code\CachedData", "AppData\Roaming\discord\Cache",
                     "AppData\Roaming\Spotify", "AppData\Local\Spotify", "AppData\Local\Plex Media Server", "AppData\Roaming\Kodi", "AppData\Local\jellyfin",
                     "AppData\Local\Temp", "OneDrive", "Dropbox", "Google Drive") {
        $p = Join-Path $u $rel; if (Test-Path -LiteralPath $p) { DirInfo $p }
    }
}
$caches += foreach ($p in "C:\ProgramData\Jellyfin", "C:\ProgramData\Plex", "C:\ProgramData\Docker", "C:\ProgramData\chocolatey", "C:\ProgramData\Sonarr", "C:\ProgramData\Radarr",
                         "C:\ProgramData\Prowlarr", "C:\ProgramData\Lidarr", "C:\ProgramData\Readarr", "C:\ProgramData\Bazarr", "C:\ProgramData\SABnzbd", "C:\Tools",
                         "C:\Python*", "C:\Android", "C:\Program Files\Android", "C:\Program Files\Docker", "C:\msys64", "C:\cygwin64", "C:\Strawberry",
                         "C:\Program Files (x86)\Steam", "C:\Program Files\Epic Games", "C:\Program Files (x86)\Origin Games", "C:\Program Files\EA Games",
                         "C:\Program Files (x86)\Ubisoft", "C:\XboxGames", "C:\Riot Games", "C:\Battle.net", "C:\GOG Games") {
    Get-Item $p -Force | ForEach-Object { DirInfo $_.FullName }
}
Tbl ($caches | Where-Object { $_.Bytes -gt 50MB } | Sort-Object Bytes -Descending | Select-Object Path, Size, Files, NewestFile)

Head "WSL / VIRTUAL DISKS / VM IMAGES ON C:"
Tbl (Get-ChildItem C:\Users -Recurse -Force -Include *.vhdx, *.vhd, *.vmdk, *.qcow2, *.iso, *.img -ErrorAction SilentlyContinue |
    Where-Object Length -gt 200MB | Sort-Object Length -Descending | Select-Object FullName, @{n='Size';e={GB $_.Length}}, LastWriteTime)
Add "WSL distros:"; Add ((wsl.exe -l -v 2>&1 | Out-String) -replace "`0", "").Trim()

# ---------------------------------------------------------------- big + old files
Head "BIGGEST FILES ON C: (500 MB and up)"
[Walk]::Big.Clear()
$f = 0L; $n = [DateTime]::MinValue
$cTotal = [Walk]::Size("C:\", [ref]$f, [ref]$n)
Add "Counted by walking the drive: $(GB $cTotal) in $f files (files Windows locks or hides from admin aren't counted)."
Tbl ([Walk]::Big | Sort-Object Value -Descending | Select-Object -First 60 @{n='Size';e={GB $_.Value}}, @{n='Modified';e={ (Get-Item -LiteralPath $_.Key -Force).LastWriteTime.ToString('yyyy-MM-dd') }}, @{n='File';e={$_.Key}})

Head "INSTALLERS / ARCHIVES / DISK IMAGES LEFT AROUND IN USER FOLDERS"
Tbl (Get-ChildItem C:\Users -Recurse -Force -File -Include *.iso, *.zip, *.7z, *.rar, *.msi, *.exe, *.tar, *.gz -ErrorAction SilentlyContinue |
    Where-Object { $_.Length -gt 100MB -and $_.FullName -notmatch '\\AppData\\' } | Sort-Object Length -Descending | Select-Object -First 40 FullName, @{n='Size';e={GB $_.Length}}, LastWriteTime)

Head "MEDIA FILES ON C: (video that could live on another drive)"
Tbl (Get-ChildItem C:\Users -Recurse -Force -File -Include *.mkv, *.mp4, *.avi, *.mov, *.m4v, *.ts, *.wmv -ErrorAction SilentlyContinue |
    Where-Object { $_.Length -gt 200MB } | Sort-Object Length -Descending | Select-Object -First 40 FullName, @{n='Size';e={GB $_.Length}}, LastWriteTime)

# ---------------------------------------------------------------- startup / services / tasks
Head "STARTUP ITEMS"
Tbl (Get-CimInstance Win32_StartupCommand | Select-Object Name, Location, Command)
Add "(Startup items disabled in Task Manager are still listed above; see Task Manager > Startup.)"

Head "NON-MICROSOFT SERVICES"
$svc = Get-CimInstance Win32_Service | ForEach-Object {
    $exe = ExePath $_.PathName
    $co = if ($exe -and (Test-Path -LiteralPath $exe)) { (Get-Item -LiteralPath $exe).VersionInfo.CompanyName } else { "" }
    [pscustomobject]@{ Name=$_.Name; Display=$_.DisplayName; Start=$_.StartMode; State=$_.State; Company=$co
                       Missing=$(if ($exe -and $exe -match '^[A-Za-z]:\\' -and -not (Test-Path -LiteralPath $exe)) { "EXE MISSING" } else { "" }); Exe=$exe }
}
Tbl ($svc | Where-Object { $_.Company -notmatch 'Microsoft' -and $_.Exe -notmatch '\\Windows\\(System32|SysWOW64)\\svchost' } | Sort-Object Start, Name | Select-Object Name, Display, Start, State, Company, Missing, Exe)
Head "SERVICES WHOSE PROGRAM NO LONGER EXISTS (abandoned)"
Tbl ($svc | Where-Object Missing | Select-Object Name, Display, Start, State, Exe)

Head "NON-MICROSOFT SCHEDULED TASKS"
$tasks = Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | ForEach-Object {
    $a = $_.Actions | Select-Object -First 1; $exe = ExePath $a.Execute
    $info = $_ | Get-ScheduledTaskInfo
    [pscustomobject]@{ Task="$($_.TaskPath)$($_.TaskName)"; State=$_.State; LastRun=$info.LastRunTime; Result=('0x{0:X}' -f $info.LastTaskResult)
                       Missing=$(if ($exe -and $exe -match '^[A-Za-z]:\\' -and -not (Test-Path -LiteralPath $exe)) { "EXE MISSING" } else { "" }); Runs="$($a.Execute) $($a.Arguments)" }
}
Tbl ($tasks | Sort-Object Task)

# ---------------------------------------------------------------- misc health
Head "MISC"
Add "Windows Search index size: $((DirInfo 'C:\ProgramData\Microsoft\Search\Data').Size)"
Add "OneDrive running: $([bool](Get-Process OneDrive -ErrorAction SilentlyContinue))"
Add "Storage Sense enabled: $((Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\StorageSense\Parameters\StoragePolicy').'01')"
Add "Compact OS: $((compact /compactos:query | Out-String).Trim())"
Add; Add "Recent disk / NTFS errors in the System log (last 30 days):"
Tbl (Get-WinEvent -FilterHashtable @{ LogName='System'; ProviderName='disk','Ntfs','storahci','stornvme','volmgr'; Level=1,2,3; StartTime=(Get-Date).AddDays(-30) } -MaxEvents 25 |
    Select-Object TimeCreated, ProviderName, Id, @{n='Message';e={ ($_.Message -split "`n")[0] }})
Add; Add "Recent app crashes (last 14 days, top 15 by count):"
Tbl (Get-WinEvent -FilterHashtable @{ LogName='Application'; Id=1000; StartTime=(Get-Date).AddDays(-14) } -MaxEvents 500 |
    ForEach-Object { $_.Properties[0].Value } | Group-Object | Sort-Object Count -Descending | Select-Object -First 15 Count, Name)

Add; Add "END OF REPORT"
[IO.File]::WriteAllText($out, $sb.ToString(), [Text.Encoding]::UTF8)
Write-Host ""
Write-Host "Done. Report saved to: $out" -ForegroundColor Green
Write-Host "Nothing on the PC was changed." -ForegroundColor Green
Start-Process notepad.exe $out
Read-Host "Press Enter to close"
