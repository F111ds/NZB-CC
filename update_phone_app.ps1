<#
  NZB Command Center - phone app updater (kept for old shortcuts).

  This now just runs update_all.ps1, which updates the phone server, its page files, HAL and
  everything else in one go, keeps a backup of each file it replaces, and restarts the server.
  If update_all.ps1 isn't in this folder yet, it is downloaded from GitHub first.
  You can also use Update everything in the app (System), or run update_all.ps1 yourself.
#>
& {
$Repo = "F111ds/NZB-CC"; $Branch = "main"
$Here = $PSScriptRoot
if (-not $Here) { $Here = (Get-Location).Path }
$All = Join-Path $Here "update_all.ps1"
if (-not (Test-Path $All)) {
    Write-Host "Downloading update_all.ps1..." -ForegroundColor Cyan
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest "https://raw.githubusercontent.com/$Repo/$Branch/update_all.ps1" -OutFile $All -UseBasicParsing -Headers @{ "Cache-Control" = "no-cache" }
        if ((Get-Content $All -Raw) -notmatch 'update everything') { Remove-Item $All -Force; throw "the download wasn't the updater (maybe a GitHub sign-in page)" }
    } catch {
        Write-Host "Couldn't get update_all.ps1 ($($_.Exception.Message))." -ForegroundColor Red
        Write-Host "Download it from https://github.com/$Repo/blob/$Branch/update_all.ps1 into this folder and run it."
        Read-Host "`nPress Enter to close"
        return
    }
}
& $All
}
