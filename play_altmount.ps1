<#
  play_altmount.ps1 - pick a movie AltMount has ready and play it in VLC. No paths to type.

  Lists every video AltMount is serving (over its WebDAV address), you type a number, and VLC opens it.
  AltMount must be running.

  Usage (in PowerShell):
    powershell -ExecutionPolicy Bypass -File .\play_altmount.ps1
    powershell -ExecutionPolicy Bypass -File .\play_altmount.ps1 -User usenet -Port 8090
#>
param(
    [string]$User = "usenet",
    [string]$Password = "",
    [int]$Port = 8090
)

$ErrorActionPreference = "Stop"
function Say($text, $color = "Gray") { Write-Host $text -ForegroundColor $color }

if (-not $Password) {
    $sec = Read-Host "AltMount WebDAV password for '$User' (the one under Configuration > WebDAV Server)" -AsSecureString
    $Password = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec))
}
$base = "http://localhost:$Port"
$auth = "Basic " + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${User}:${Password}"))
$videoExt = '\.(mkv|mp4|m4v|avi|mov|m2ts|ts|wmv|mpg|mpeg|webm|iso)$'

function List-Folder([string]$href) {
    # WebDAV PROPFIND, one level deep: what's in this folder.
    $req = [Net.HttpWebRequest]::Create($base + $href)
    $req.Method = "PROPFIND"
    $req.Headers.Add("Depth", "1")
    $req.Headers.Add("Authorization", $auth)
    $req.Timeout = 30000
    try {
        $resp = $req.GetResponse()
    } catch [Net.WebException] {
        $code = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
        if ($code -eq 401) { Say "AltMount said the username or password is wrong." Red; exit 1 }
        if ($code -eq 0) { Say "Couldn't reach AltMount at $base. Is it running?" Red; exit 1 }
        throw
    }
    $reader = New-Object IO.StreamReader($resp.GetResponseStream())
    [xml]$doc = $reader.ReadToEnd()
    $reader.Close(); $resp.Close()
    $ns = New-Object Xml.XmlNamespaceManager($doc.NameTable)
    $ns.AddNamespace("d", "DAV:")
    $items = @()
    foreach ($r in $doc.SelectNodes("//d:response", $ns)) {
        $h = $r.SelectSingleNode("d:href", $ns).InnerText
        if ($h -match '^https?://[^/]+(/.*)$') { $h = $Matches[1] }
        if ($h.TrimEnd('/') -eq $href.TrimEnd('/')) { continue }      # the folder itself
        $isDir = $null -ne $r.SelectSingleNode(".//d:resourcetype/d:collection", $ns)
        $len = $r.SelectSingleNode(".//d:getcontentlength", $ns)
        $items += [pscustomobject]@{ Href = $h; Dir = $isDir; Size = if ($null -ne $len -and $len.InnerText) { [double]$len.InnerText } else { 0 } }
    }
    return $items
}

Say "Looking for movies in AltMount..." Cyan
$videos = @()
$todo = New-Object Collections.Queue
$todo.Enqueue(@("/webdav/", 0))
while ($todo.Count -gt 0) {
    $next = $todo.Dequeue()
    foreach ($it in (List-Folder $next[0])) {
        if ($it.Dir) {
            if ($next[1] -lt 5) { $todo.Enqueue(@($it.Href, $next[1] + 1)) }
        } elseif ([uri]::UnescapeDataString($it.Href) -match $videoExt -and [uri]::UnescapeDataString($it.Href) -notmatch '(?i)\bsample\b') {
            $videos += $it
        }
    }
}
if (-not $videos) { Say "No movies yet. Add an .nzb on AltMount's Queue > Import page first." Yellow; exit 0 }

$videos = @($videos | Sort-Object { [uri]::UnescapeDataString($_.Href) })
for ($i = 0; $i -lt $videos.Count; $i++) {
    $name = [IO.Path]::GetFileName([uri]::UnescapeDataString($videos[$i].Href.TrimEnd('/')))
    $gb = if ($videos[$i].Size) { " ({0:N1} GB)" -f ($videos[$i].Size / 1GB) } else { "" }
    Say ("  {0,2}. {1}{2}" -f ($i + 1), $name, $gb)
}
$pick = Read-Host "Number to play"
if (-not ($pick -match '^\d+$') -or [int]$pick -lt 1 -or [int]$pick -gt $videos.Count) { Say "That's not one of the numbers." Red; exit 1 }
$chosen = $videos[[int]$pick - 1]

$vlc = @("$env:ProgramFiles\VideoLAN\VLC\vlc.exe", "${env:ProgramFiles(x86)}\VideoLAN\VLC\vlc.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $vlc) { Say "VLC isn't installed (videolan.org). Install it and run this again." Red; exit 1 }
$url = "http://" + [uri]::EscapeDataString($User) + ":" + [uri]::EscapeDataString($Password) + "@localhost:$Port" + ($chosen.Href -replace " ", "%20")
Say "Opening it in VLC..." Green
Start-Process -FilePath $vlc -ArgumentList "`"$url`""
