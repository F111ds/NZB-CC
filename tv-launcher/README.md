# NZB-CC TV launcher

The CleanYourTV launcher (MIT, see `LICENSE-cleanyourtv`; upstream commit in `UPSTREAM_COMMIT`) with these changes:

- **Kodi row** above the dock: **recently watched** in Kodi (movies and episodes, the last played first), five
  posters centred on a glass shelf, each with a bar showing how far in you are; OK opens Kodi and resumes it, even
  when Kodi wasn't running. Anything without a poster is left out. The list comes from `mobile_server.py` on the PC
  (`/api/tv/watched`), falls back to Kodi on the TV, and is saved on the TV so it shows at once after power-on.
- **Weather** as a small reading at the start of the top bar (Open-Meteo, no key).
- **Sora** as the launcher's font (OFL, see `launcher/FONT-LICENSE-OFL.txt`).
- **Smaller top bar**: 34dp pills with 18dp icons; Settings keeps its label; the bar's right edge is as far from
  the screen edge as the clock's first digit is from the left.
- **Clock and date on one line** (languages that break the date after the weekday get a comma instead).
- **Single-row glass dock** pinned to the bottom, nothing peeking below it (`single_row`, `dock_band_all`,
  `dock_glass` on by default in this build, and set by the install script).
- **Smaller app tiles** (`dock_tile_scale`, 0.8 by default).

- **HAL 9000 screensaver** (`HalDream`), beside the photo slideshow: HAL's eye, the clock, what HAL is saying,
  what's downloading or any alerts, and live readings (CPU, memory, internet, Pi-hole, Uptime Kuma, fullest drive)
  from the phone server's `/hal/api/brief`, every 30 s. HAL says his line through the TV's text-to-speech when it
  starts and then at most every 30 minutes, never 10 PM to 8 AM; the eye flares on each word and the line types out.
  Everything drifts a little every 3 minutes and dims at night (burn-in). Pick it in the TV's **Settings › Device
  Preferences › Screen saver**: **NZB-CC Photos** or **HAL 9000**. Installs keep whichever you picked.

Updating: **Update everything** (the phone app, or `update_all.ps1`) puts a new `NZB-CC-TV-Launcher.apk` on the TV by
itself (`install-on-tv.ps1 -UpdateOnly`: the app only; TV settings and the screensaver choice stay). It uses the TV
address from the last install (`tv_host.txt`), else Kodi's address.

The wallpaper is turned off by the install script (a `config.json` setting), not by the build.

## Install (on the PC that runs mobile_server.py)

```powershell
cd tv-launcher
powershell -ExecutionPolicy Bypass -File .\install-on-tv.ps1 -TvHost <TV IP address>
```

It saves the TV's current `config.json` to `backup\`, installs `NZB-CC-TV-Launcher.apk`, and adds these keys to the
TV's `config.json` (every other setting is kept):

| Key | Value |
|---|---|
| `wallpaper` | `false` |
| `background_color` | `#070B14` |
| `dock_glass` | `true` |
| `single_row`, `dock_band_all` | `true` |
| `rows_below_dock` | `0` |
| `date_below_clock` | `false` |
| `kodi_row_size` | `5` |
| `kodi_row_server` | `http://<this PC>:8765` |
| `kodi_row_token` | the phone server's access code |

Live exchange rate: add `-FxKey <AllRatesToday key>` to the install command once; it is saved in
`..\fx_api_key.txt` (git-ignored) and written to the TV as `fx_key`. `fx_refresh_minutes` (default 10) sets how
often it is read while Home is on screen. Without a key, or if the key runs out, the TV shows the daily ECB rate.

Other optional keys: `weather_lat`, `weather_lon` (Querétaro City by default), `weather_unit` (`c`/`f`),
`dock_tile_scale` (0.6-1), `kodi_package` (`org.xbmc.kodi`), `kodi_local_url`
(`http://127.0.0.1:8080`), `kodi_local_user`, `kodi_local_password`.

## Build

JDK 17+ and Android SDK 34, then from `launcher/`: `gradle assembleRelease`. The APK is signed with
`nzbcc-launcher.keystore` (kept here on purpose), so every rebuild installs over the last one.
