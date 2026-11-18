---
name: nzbcc-tv-launcher
description: Change, build, mock up and ship the NZB-CC TV launcher (the customised CleanYourTV home screen in tv-launcher/) and the TV parts of mobile_server.py for the owner's Sony BRAVIA. Use for any request about the TV home screen, its Kodi row, dock, top bar, screensaver, weather/exchange rate, TV clean-up scripts, or the phone server's /api/tv endpoints.
---

# NZB-CC TV launcher

The owner's Sony BRAVIA (Android TV 9, model "BRAVIA 4K GB ATV3") runs a customised CleanYourTV launcher built from
`tv-launcher/launcher/`. NZB Command Center's phone server (`mobile_server.py`, on the Windows PC "HAL9000") feeds it
Kodi data, posters and screensaver photos. The owner reviews everything as a **mock-up first**, then installs from the
PC with PowerShell commands you hand them. You (a cloud session) can't reach the PC, the TV or the home network.

## Ground rules from the owner

- **Mock-up before building anything visible.** Render a PNG of the home screen (see "Mock-ups") and send it with
  `SendUserFile`; build only after "build it". Behaviour-only fixes can be built directly.
- **"Hold on" means stop.** Don't build until they say go. When they say "just mock it up", do only that.
- **Measure, don't eyeball.** They notice uneven spacing and cut-off text. Measure gaps in the mock-up with
  Playwright `getBoundingClientRect()` and quote the numbers; test the longest case (e.g. "Wednesday", "12:59 PM").
- **Hand them copy-paste PowerShell for Windows PowerShell 5.1**, one command per fenced block (tag `bash` per the
  harness rule), full paths via `$env:USERPROFILE\Desktop\NZB Command Center`, never `cd .\tv-launcher` relative
  (they once ended up in `tv-launcher\tv-launcher`). Use a **new download folder name each time**
  (`nzbcc-tv31`, `nzbcc-tv32`, ...) so stale extractions never get reused.
- Say plainly what you couldn't test (no TV, no PowerShell here, feeds unreachable from the proxy).
- They speak to the session by voice: "Cody" = Kodi, "doc" = dock, "tide/time" = the clock. Messages can be cut off;
  ask about the missing part in one line.

## Layout of the code

| Piece | Where |
|---|---|
| Launcher source (Java, no Kotlin) | `tv-launcher/launcher/app/src/main/java/com/gathin/tvhome/` |
| Home layout | `res/layout/activity_main.xml` (most NZB-CC changes are also applied in code in `MainActivity`) |
| Defaults (theme keys) | `res/values/config.xml` (search `NZB-CC default`) |
| NZB-CC-only settings | `HomeExtras.java` (read from the TV's `config.json`; documents every key in its header) |
| Kodi row | `KodiRow.java` (+ `MainActivity.layoutKodiRow`, `onKodiItem*`) |
| Weather / forecast | `WeatherNow.java`, `MainActivity.showForecast` |
| Exchange rate | `ExchangeRate.java` (live AllRatesToday with `fx_key`, else daily ECB) |
| Photo screensaver | `PhotoDream.java` (registered in `AndroidManifest.xml`) |
| Ready-to-watch note | `DownloadNotes.java` + `MainActivity.showNextReady` |
| News ticker (off) | `NewsFeed.java`, `TickerView.java` (`"news": true` turns it on) |
| Built APK (committed) | `tv-launcher/NZB-CC-TV-Launcher.apk` |
| Installer | `tv-launcher/install-on-tv.ps1` (backs up config.json, installs, writes keys with `SetKey`, prints version) |
| Other TV tools | `do-everything.ps1`, `list-tv-apps.ps1` (read-only), `switch-off-tv-junk.ps1`, `remove-tv-apps.ps1` |
| Phone server TV endpoints | `mobile_server.py`: `/api/tv/watched` (`?fresh=1`), `/api/tv/recent`, `/api/tv/poster`, `/api/tv/play` (`from_start`), `/api/tv/mark_watched`, `/api/tv/finished`, `/api/tv/photos`, `/api/tv/photo`; plus the owner's Sony power/Kodi buttons (`/api/tv`, from branch `tv-power-kodi`) |
| Tests | `tests/test_tv_watched.py`, `test_tv_photos.py`, `test_tv_finished.py`, `test_tv_row.py`, `test_tv_control.py`, `test_mobile.py` |

The current look: one-line header (time 18sp | date | rate), top-right weather pill + round icons (Inputs, Wi-Fi with
a green/red VPN lock for NordVPN, app library, Settings) 36dp from the right edge; Kodi "Recently watched" row of 5
posters (205dp max) centred with equal gaps to the header and the dock; a compact centred single-row glass dock with
only Netflix, Prime Video, YouTube, Kodi (`home_apps`) shown as bare icons (`dock_icons_only`); time-of-day sky;
photo screensaver from a PC folder.

## Build

The container has no Android SDK until you install it:

```bash
mkdir -p /opt/android-sdk && cd /opt/android-sdk
curl -sS -o clt.zip https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip
unzip -q clt.zip && mkdir -p cmdline-tools/latest && mv cmdline-tools/{bin,lib,NOTICE.txt,source.properties} cmdline-tools/latest/
yes | cmdline-tools/latest/bin/sdkmanager --sdk_root=/opt/android-sdk "platforms;android-34" "build-tools;34.0.0" "platform-tools"
echo "sdk.dir=/opt/android-sdk" > /home/user/NZB-CC/tv-launcher/launcher/local.properties   # git-ignored
```

Every release: bump `versionCode` and `versionName` ('3.5-nzbcc.N') in `tv-launcher/launcher/app/build.gradle`, then

```bash
cd tv-launcher/launcher && ./gradlew assembleRelease -q
cp app/build/outputs/apk/release/app-release.apk ../NZB-CC-TV-Launcher.apk
/opt/android-sdk/build-tools/34.0.0/aapt2 dump badging ../NZB-CC-TV-Launcher.apk | head -1
```

The APK is signed with `launcher/nzbcc-launcher.keystore` (committed on purpose), so it installs over the TV's copy
with no prompt. Run the Python tests after any `mobile_server.py` change (`python3 -I tests/<name>.py`); tests that
import tkinter/psutil fail in the container regardless.

Before pushing: `git fetch origin <branch> && git merge --no-edit FETCH_HEAD`. Other sessions of the owner push to the
same branch (one added the live exchange rate). If they did, rebuild the APK so it contains both.

## Mock-ups

Start from `mockup-template.html` in this skill folder: the home screen as shipped in 3.5-nzbcc.29 (one-line header,
icon pills, five posters, icons-only dock, evening sky). Copy it and `tv-launcher/launcher/app/src/main/res/font/sora.ttf`
into the scratchpad, edit the copy, then `node render-mockup.js page.html out.png` (Playwright is preinstalled; it
renders 960x540 CSS px, the TV's dp grid, at 2x). Measurements if you need to build something new: header padding 40dp left / 36dp right, top 26dp; pills 34dp tall, 8dp apart;
Kodi shelf margin 46dp + padding 14dp, poster cell = poster width + 12dp; dock band = tile + 14dp padding; popups use
`glass_panel` (#2A2A2E → #161618, 1dp #33FFFFFF border, 20dp corners, opaque).

- Draw stand-ins, never real logos/posters; label "sample numbers" in a faint corner note.
- Draw app logos centred like the real banner/icon artwork. Never place raw text in a tile corner (an early mock-up
  showed "prime video" overflowing and confused the owner).
- For layout questions, send two states: an app selected in the dock (app name visible) and a poster selected
  (app name hidden, info line + "time left" chip).

## Installing (what to give the owner)

Launcher only:

```powershell
$nzb = "$env:USERPROFILE\Desktop\NZB Command Center"; Invoke-WebRequest https://github.com/F111ds/NZB-CC/archive/refs/heads/<branch>.zip -OutFile "$env:TEMP\nzbcc-tvNN.zip"; Expand-Archive "$env:TEMP\nzbcc-tvNN.zip" "$env:TEMP\nzbcc-tvNN" -Force; $src = (Get-ChildItem "$env:TEMP\nzbcc-tvNN" -Directory | Select-Object -First 1).FullName; Copy-Item "$src\tv-launcher\*" "$nzb\tv-launcher" -Recurse -Force
cd "$env:USERPROFILE\Desktop\NZB Command Center\tv-launcher"; powershell -ExecutionPolicy Bypass -File .\install-on-tv.ps1 -TvHost <TV IP>
```

If `mobile_server.py` changed, also copy `"$src\mobile_server.py"` into `$nzb` and restart the server (stop every
`python*` process whose CommandLine matches `*mobile_server*`, then `Start-Process python -ArgumentList 'mobile_server.py'`
in `$nzb`). **Say explicitly whether a server restart is needed.** Forgetting to copy `mobile_server.py` once left the
phone app on an old file. The install prints `Launcher on the TV now: versionName=...`; ask them to check it.
A TV screenshot (better than phone photos):
`.\tools\venv\Scripts\python.exe -I tools\tv.py --screenshot "$env:USERPROFILE\Desktop\tv-home.png"` with `$env:TV_HOST` set.

## Pitfalls already hit (don't repeat)

- **Windows PowerShell 5.1 strips double quotes inside arguments to native programs.** Send TV shell commands that
  contain `"` through `tools\tv.py --batch` on stdin, not as an argument.
- **`$PSScriptRoot` can be empty in `param()` defaults.** Set derived paths after `param()`.
- **`View.setY` is a translation.** Anything else that calls `setTranslationY` on the same view (a scroll listener did)
  moves it to the top of the screen. The Kodi shelf keeps `kodiBaseY` and ignores sideways dock scrolls.
- **Move views with layout margins, not `translationX`, when their neighbours must make room.** Shifting the icon
  bar with translation cut off the exchange rate.
- **Two `ViewPropertyAnimator` calls on one view share a duration.** Use separate `ObjectAnimator`s (screensaver fade
  vs. zoom).
- **The pill's `drawableTint` recolours every compound drawable.** Coloured icons in a pill need a wrapper that ignores
  tint (`FixedTintDrawable`).
- **`bringToFront` on a screensaver layer covers the clock overlay.** Cross-fade by alpha only.
- **Several CEC devices share HDMI 3** (Denon AVR-X2700H on ARC, Sony UBP-X700, PS5). Label each by its own CEC name.
- **Replacing `mobile_server.py` drops features from other branches.** The owner's "TV on + Kodi" buttons live on
  `tv-power-kodi`; it is merged into this work. Check `git ls-remote --heads origin` for other branches touching the
  same file before shipping it. Older server versions that didn't know the `tv_*` settings could drop them on save.
- **"Recently watched" lag:** the TV asks every 5 min, and the server caches for 2 min. On Home resume the TV calls
  `refreshNow()` and the server reads Kodi at once (`?fresh=1`). Items without a poster are hidden on purpose.
- Can't reach from the container: news/rate feeds, the TV, the PC. GitHub release downloads (e.g. PowerShell for
  syntax checks) were blocked by policy; review `.ps1` files by hand for PS 5.1 compatibility.

## TV facts

- Sony BRAVIA, Android 9, MediaTek. Home = `com.gathin.tvhome/.MainActivity`. ADB over network via `tools/tv.py`
  (adb_shell in `tools\venv`).
- Speaker setting is `system speaker_type` (`audio_system` / TV speaker). Screensaver start time is
  `system screen_off_timeout` (ms). `hdmi_system_audio_control_enabled=1`.
- Already disabled before this work: Samba TV, Sony promos/Sony Select, Google TV recommendations, the stock tvlauncher.
  `switch-off-tv-junk.ps1` handles 7 more (interactivetvutil, launcher customisers, backdrop, leanbacklauncher).
- Never offer to remove: the launcher, Kodi, Netflix, Prime Video, YouTube, Play Store, Settings, Chromecast
  (`mediashell`), voice (`katniss`), GMS, Sony `b2b.*`, MediaTek agents.
- NordVPN (`com.nordvpn.android`) drives the VPN lock (`vpn_app`). The screensaver folder is the first line of
  `tv_photos_folder.txt` next to `mobile_server.py` (default: Pictures). The owner uses a mapped `Z:` share.
