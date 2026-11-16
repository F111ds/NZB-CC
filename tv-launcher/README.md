# NZB-CC TV launcher

The CleanYourTV launcher (MIT, see `LICENSE-cleanyourtv`; upstream commit in `UPSTREAM_COMMIT`) with three additions:

- **Kodi row** above the dock: the newest movies with their posters; OK opens Kodi and plays the movie, even when
  Kodi wasn't running. The list comes from `mobile_server.py` on the PC (`/api/tv/recent`), falls back to Kodi on the
  TV, and is saved on the TV so it shows at once after power-on.
- **Centred home** (`centered_home`, on by default): posters across the middle with the selected movie under them, a small centred dock of square app icons, the weather in the top bar.
- **Weather**: temperature, high and low (Open-Meteo, no key).
- **Smaller app tiles** (`dock_tile_scale`, 0.8 by default) to give the posters room.

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
| `kodi_row_server` | `http://<this PC>:8765` |
| `kodi_row_token` | the phone server's access code |

Other optional keys: `weather_lat`, `weather_lon` (Querétaro City by default), `weather_unit` (`c`/`f`),
`kodi_row_size` (12), `dock_tile_scale` (0.6-1), `kodi_package` (`org.xbmc.kodi`), `kodi_local_url`
(`http://127.0.0.1:8080`), `kodi_local_user`, `kodi_local_password`.

## Build

JDK 17+ and Android SDK 34, then from `launcher/`: `gradle assembleRelease`. The APK is signed with
`nzbcc-launcher.keystore` (kept here on purpose), so every rebuild installs over the last one.
