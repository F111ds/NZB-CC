<#
  NZB Command Center - back up your settings to GitHub, WITHOUT any keys or passwords.

  Right-click > Run with PowerShell (from your NZB Command Center folder), or paste the one-line
  command you were given. Safe to run again any time: it only uploads when something changed.

  What is backed up (into the folder settings-backup\ of your GitHub repository):
    nzb_manager_config.json, mobile_settings.json, system_center_config.json, nzb_daily_stats.json,
    tv_photos_folder.txt and the newest TV launcher settings (tv-launcher\backup\config-*.json).
  What is REMOVED first, because the repository is public:
    every API key, token, password and Pre-Shared Key (also anything stored as "dpapi:..."),
    your ntfy alert topic address (it works like a password), your search history and saved searches.
  After a restore you enter new keys in the apps (SABnzbd, indexers, Kodi password, Jellyfin, TV).

  Uploading needs Git for Windows; the first upload asks you to sign in to GitHub in your browser.
  The upload is refused if any removed value is still found anywhere in the backup.
#>
param([switch]$IncludeSearches)

& {
$ErrorActionPreference = "Stop"
$Repo = "https://github.com/F111ds/NZB-CC.git"
function Say($t, $c = "Gray") { Write-Host $t -ForegroundColor $c }
# Runs git.exe (a helper named "Git" would call itself: PowerShell prefers functions and ignores case).
# -c safe.directory: the backup folder may have been created from an administrator window (owned by
# "Administrators") and later used as you, or the other way round; git then refuses it as "dubious
# ownership". This marks only this script's own folder as safe, for its own commands only.
$SafeDir = (Join-Path $env:LOCALAPPDATA "NZB-CC-backup\repo") -replace '\\', '/'
function RunGit { $ErrorActionPreference = 'Continue'; $out = & git.exe -c "safe.directory=$SafeDir" @args 2>&1 | Out-String; $script:gitCode = $LASTEXITCODE; return $out.Trim() }

# Field names that hold secrets (matched on whole name parts: sab_api_key, kodi_password, tv_psk, ...).
$SecretName = '(^|_)(api_?key|apikey|key|keys|token|tokens|password|passwd|pass|pwd|psk|secret|secrets|auth|cookie|credential|credentials)($|_)'
$AlwaysDrop = @('ntfy_url')                       # the ntfy topic address works like a password
$Private    = @('search_history', 'saved_searches', 'recent_searches')
$removed = New-Object System.Collections.Generic.List[string]

function Clean($node) {
    if ($node -is [System.Management.Automation.PSCustomObject]) {
        foreach ($p in @($node.PSObject.Properties)) {
            $n = $p.Name.ToLower()
            $v = $p.Value
            if (($n -match $SecretName) -or ($AlwaysDrop -contains $n) -or ($v -is [string] -and $v -like 'dpapi:*')) {
                if ($v -is [string] -and $v.Length -ge 4) { $removed.Add($v) }
                $node.$($p.Name) = ""
            } elseif (-not $IncludeSearches -and ($Private -contains $n)) {
                $node.$($p.Name) = @()
            } else {
                $node.$($p.Name) = Clean $v
            }
        }
        return $node
    }
    if ($node -is [System.Array]) { return ,@($node | ForEach-Object { Clean $_ }) }
    return $node
}

try {
    Say "NZB Command Center - settings backup (no keys)" Cyan
    $Folder = $null
    foreach ($f in @($PSScriptRoot, (Get-Location).Path, "$env:USERPROFILE\Desktop\NZB-Command-Center")) {
        if ($f -and (Test-Path (Join-Path $f "nzb_manager_config.json"))) { $Folder = $f; break }
    }
    if (-not $Folder) { $Folder = (Read-Host "Path to your NZB Command Center folder").Trim('"', ' ') }
    if (-not (Test-Path (Join-Path $Folder "nzb_manager_config.json"))) { throw "No nzb_manager_config.json in $Folder." }
    Say "Folder: $Folder"

    # --- Git ---
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Say "Git isn't installed. Installing it (one time)..." Yellow
        $ErrorActionPreference = 'Continue'
        winget install --id Git.Git -e --silent --accept-package-agreements --accept-source-agreements
        $ErrorActionPreference = 'Stop'
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
            throw "Couldn't install Git automatically. Install it from https://git-scm.com/download/win, then run this again."
        }
    }

    # --- A copy of your repository just for backups (kept here, separate from your program files) ---
    $work = Join-Path $env:LOCALAPPDATA "NZB-CC-backup\repo"
    if (-not (Test-Path (Join-Path $work ".git"))) {
        New-Item -ItemType Directory -Force -Path (Split-Path $work) | Out-Null
        Say "Getting a copy of your repository (one time)..."
        $o = RunGit clone --quiet $Repo $work
        if ($gitCode -ne 0) { throw "Couldn't download the repository: $o" }
    }
    Push-Location $work
    try {
        $o = RunGit fetch --quiet origin main; if ($gitCode -ne 0) { throw "Couldn't reach GitHub: $o" }
        $o = RunGit checkout --quiet -B main origin/main; if ($gitCode -ne 0) { throw $o }
        $o = RunGit reset --quiet --hard origin/main; if ($gitCode -ne 0) { throw $o }
        # Commits from this script are signed with a neutral name, not your personal email.
        $null = RunGit config user.name "NZB Command Center backup"
        $null = RunGit config user.email "nzbcc-backup@users.noreply.github.com"

        # --- Clean copies of the settings files ---
        $dest = Join-Path $work "settings-backup"
        if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
        New-Item -ItemType Directory -Path $dest | Out-Null
        $jsons = @("nzb_manager_config.json", "mobile_settings.json", "system_center_config.json", "nzb_daily_stats.json")
        $tvBackup = Get-ChildItem (Join-Path $Folder "tv-launcher\backup") -Filter "config-*.json" -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
        $sources = @($jsons | ForEach-Object { @{ src = (Join-Path $Folder $_); name = $_ } })
        if ($tvBackup) { $sources += @{ src = $tvBackup.FullName; name = "tv-launcher-config.json" } }
        $saved = @()
        foreach ($s in $sources) {
            if (-not (Test-Path $s.src)) { continue }
            $obj = Get-Content $s.src -Raw -Encoding UTF8 | ConvertFrom-Json
            $clean = Clean $obj
            # UTF-8 WITHOUT a byte-order mark: Windows PowerShell's Set-Content -Encoding UTF8 adds one,
            # and the apps would then read a restored file as damaged.
            [IO.File]::WriteAllText((Join-Path $dest $s.name), ($clean | ConvertTo-Json -Depth 30), (New-Object System.Text.UTF8Encoding $false))
            $saved += $s.name
        }
        $photos = Join-Path $Folder "tv_photos_folder.txt"
        if (Test-Path $photos) { Copy-Item $photos (Join-Path $dest "tv_photos_folder.txt"); $saved += "tv_photos_folder.txt" }
        @"
# Settings backup (keys removed)

Made by backup_settings.ps1 on $(Get-Date -Format "yyyy-MM-dd HH:mm"). Every API key, token, password and
Pre-Shared Key was removed, as were the ntfy alert address and search history, because this
repository is public.

To restore: copy these files back into your NZB Command Center folder (tv-launcher-config.json is
the TV launcher's config.json), then enter new keys: SABnzbd and indexers in the desktop app's
Settings, the Kodi password in its Kodi tab, Jellyfin in its Media Server tab, the TV's
Pre-Shared Key in the phone app's Kodi tab. A new phone access code is made automatically.
"@ | Set-Content (Join-Path $dest "README.md") -Encoding UTF8

        # --- Safety check: no removed value may appear anywhere in the backup ---
        $all = (Get-ChildItem $dest -File | ForEach-Object { Get-Content $_.FullName -Raw }) -join "`n"
        $access = Join-Path $Folder "mobile_access_key.txt"
        if (Test-Path $access) { $removed.Add((Get-Content $access -Raw).Trim()) }
        foreach ($secret in $removed) {
            if ($secret -and $all.Contains($secret)) { throw "Safety check failed: a key was still found in the backup. Nothing was uploaded." }
        }
        if ($all -match 'dpapi:') { throw "Safety check failed: an encrypted key was still found in the backup. Nothing was uploaded." }
        Say "Backed up (keys removed): $($saved -join ', ')" Green

        # --- Upload ---
        $null = RunGit add -f -- settings-backup
        $null = RunGit diff --cached --quiet
        if ($gitCode -eq 0) { Say "`nNothing changed since the last backup. Nothing to upload." Green; return }
        $o = RunGit commit --quiet -m "Settings backup $(Get-Date -Format 'yyyy-MM-dd HH:mm') (keys removed)"
        if ($gitCode -ne 0) { throw "Couldn't save the backup: $o" }
        Say "Uploading to GitHub (the first time, sign in to GitHub in the browser window that opens)..."
        $o = RunGit push --quiet origin HEAD:main
        if ($gitCode -ne 0) { throw "The upload failed: $o`nIf GitHub asked you to sign in, finish that and run this again." }
        Say "`nDone. Your settings are on GitHub in settings-backup\ (keys removed)." Cyan
    } finally { Pop-Location }
} catch {
    Say "`n$($_.Exception.Message)" Red
}
}
Read-Host "`nPress Enter to close"
