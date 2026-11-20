<#
Finds the way your TV accepts a search from HAL ("Hey Siri, Ask HAL 9000, search Netflix for Stranger Things").

TV apps differ in which kind of search request they act on, and some accept one and then ignore it, so the only
way to know is to try them on the TV and look. This script tries each way in turn: it goes to the TV's home
screen, sends one search, and asks you whether the TV showed results. The first one you say yes to is saved
(in ..\tv_search_methods.json), and HAL uses it from then on. Nothing else on the TV is changed.

Run on HAL9000, from this folder, in PowerShell, with the TV on and in view:

    powershell -ExecutionPolicy Bypass -File .\try-tv-search.ps1

Options:
    -App netflix      only test Netflix (netflix, prime, youtube, or global = the TV's own search). Default: all.
    -Query "Coco"     what to search for (default: Stranger Things)
    -TvHost 1.2.3.4   the TV's address (default: the one the TV installer remembered)
    -Reset            forget what was saved (HAL goes back to opening the app only)
#>
param(
    [string]$App = 'all',
    [string]$Query = 'Stranger Things',
    [string]$TvHost = '',
    [switch]$Reset
)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$saveFile = Join-Path (Split-Path $PSScriptRoot -Parent) 'tv_search_methods.json'

if ($Reset) {
    if (Test-Path $saveFile) { Remove-Item $saveFile }
    Write-Host 'Forgotten. HAL opens Netflix and Prime without searching until you run this again.' -ForegroundColor Green
    return
}

if (-not $TvHost) {
    $hostFile = Join-Path (Split-Path $PSScriptRoot -Parent) 'tv_host.txt'
    if (Test-Path $hostFile) { $TvHost = (Get-Content $hostFile -Raw).Trim() }
}
if (-not $TvHost) { $TvHost = (Read-Host "The TV's address (e.g. 192.168.68.62)").Trim() }
$env:TV_HOST = $TvHost

$py = '.\tools\venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw 'The TV tools are not set up yet. Run install-on-tv.ps1 once first.' }
function Tv([string]$cmd) {
    $ErrorActionPreference = 'Continue'
    $out = & $py -I tools\tv.py $cmd 2>&1
    return ($out | Out-String)
}

# Each way: a name, and the TV command ({q} = the search, URL-encoded; {qs} = the search as typed).
$ways = [ordered]@{
    netflix = @(
        @('Netflix web link', "am start -a android.intent.action.VIEW -d 'https://www.netflix.com/search?q={q}' com.netflix.ninja"),
        @('Netflix app link (nflx)', "am start -a android.intent.action.VIEW -d 'nflx://www.netflix.com/search?q={q}' com.netflix.ninja"),
        @('Netflix web link, straight to its main screen', "am start -n com.netflix.ninja/.MainActivity -a android.intent.action.VIEW -d 'https://www.netflix.com/search?q={q}'"),
        @('Netflix app link, straight to its main screen', "am start -n com.netflix.ninja/.MainActivity -a android.intent.action.VIEW -d 'nflx://www.netflix.com/search?q={q}'"),
        @('Netflix search link (netflix://)', "am start -a android.intent.action.VIEW -d 'netflix://search?q={q}' com.netflix.ninja"),
        @('Android search request to Netflix', "am start -a android.intent.action.SEARCH --es query '{qs}' com.netflix.ninja"),
        @('Netflix web link (search/...)', "am start -a android.intent.action.VIEW -d 'https://www.netflix.com/search/{q}' com.netflix.ninja")
    )
    prime = @(
        @('Prime web link', "am start -a android.intent.action.VIEW -d 'https://app.primevideo.com/search?phrase={q}' com.amazon.amazonvideo.livingroom"),
        @('Prime app link (aiv)', "am start -a android.intent.action.VIEW -d 'aiv://aiv/search?phrase={q}' com.amazon.amazonvideo.livingroom"),
        @('Prime web link (primevideo.com)', "am start -a android.intent.action.VIEW -d 'https://www.primevideo.com/search/ref=atv_nb_sr?phrase={q}' com.amazon.amazonvideo.livingroom"),
        @('Android search request to Prime', "am start -a android.intent.action.SEARCH --es query '{qs}' com.amazon.amazonvideo.livingroom")
    )
    youtube = @(
        @('YouTube web link', "am start -a android.intent.action.VIEW -d 'https://www.youtube.com/results?search_query={q}' com.google.android.youtube.tv"),
        @('Android search request to YouTube', "am start -a android.intent.action.SEARCH --es query '{qs}' com.google.android.youtube.tv")
    )
    global = @(
        @("The TV's search (web search request)", "am start -a android.intent.action.WEB_SEARCH --es query '{qs}'"),
        @("The TV's search (Google app search request)", "am start -a android.intent.action.SEARCH --es query '{qs}' -p com.google.android.katniss"),
        @("The TV's search (global search, Google app)", "am start -a android.search.action.GLOBAL_SEARCH --es query '{qs}' -p com.google.android.katniss"),
        @("The TV's search (global search)", "am start -a android.search.action.GLOBAL_SEARCH --es query '{qs}'")
    )
}
$names = @{ netflix = 'Netflix'; prime = 'Prime Video'; youtube = 'YouTube'; global = "the TV's own search" }
$apps = if ($App -eq 'all') { @('netflix', 'prime', 'global', 'youtube') } else { @($App.ToLower()) }
foreach ($a in $apps) { if (-not $ways.Contains($a)) { throw "Unknown -App $a (use netflix, prime, youtube, global or all)." } }

$saved = @{}
if (Test-Path $saveFile) {
    try { (Get-Content $saveFile -Raw | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $saved[$_.Name] = $_.Value } } catch { }
}
$q = [uri]::EscapeDataString($Query)
$qs = $Query -replace "'", ''

Write-Host "Connecting to the TV at $TvHost..." -ForegroundColor Cyan
$probe = Tv '--probe'
if ($LASTEXITCODE -ne 0) { throw "Couldn't reach the TV: $($probe.Trim()). Is it on?" }

foreach ($a in $apps) {
    Write-Host "`n== $($names[$a]): searching for '$Query'" -ForegroundColor Cyan
    $found = $false
    $i = 0
    foreach ($w in $ways[$a]) {
        $i++
        Write-Host "`n  Try $i of $($ways[$a].Count): $($w[0])"
        Tv 'input keyevent 3' | Out-Null          # Home first, so each try starts the same way
        Start-Sleep -Seconds 2
        $cmd = $w[1].Replace('{q}', $q).Replace('{qs}', $qs)
        $out = (Tv $cmd).Trim()
        if ($out -match 'Error|unable to resolve|does not exist') {
            Write-Host "  The TV refused it: $($out -split "`n" | Select-Object -Last 1)" -ForegroundColor DarkGray
            continue
        }
        Start-Sleep -Seconds 6
        $ans = (Read-Host "  Look at the TV: did it show results for '$Query'? (y = yes, n = no, s = skip $($names[$a]))").Trim().ToLower()
        if ($ans -eq 's') { break }
        if ($ans -eq 'y') {
            $saved[$a] = $w[1]
            $found = $true
            Write-Host "  Saved. HAL will use this for $($names[$a])." -ForegroundColor Green
            break
        }
    }
    if (-not $found) { Write-Host "  No way worked for $($names[$a])." -ForegroundColor Yellow }
}
Tv 'input keyevent 3' | Out-Null

($saved | ConvertTo-Json) | Set-Content -Path $saveFile -Encoding UTF8
Write-Host "`nDone. Saved in $saveFile" -ForegroundColor Green
Write-Host "HAL picks this up at once (no restart). Try: 'Hey Siri, Ask HAL 9000, search Netflix for Stranger Things'."
if (-not $saved.Contains('netflix') -and -not $saved.Contains('prime') -and -not $saved.Contains('global')) {
    Write-Host "Nothing worked for Netflix, Prime or the TV's search: HAL will just open those apps. Use the remote's mic button to search." -ForegroundColor Yellow
}
