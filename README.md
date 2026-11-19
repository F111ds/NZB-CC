mobile nzb command center

Screenshots of every page of the HAL 9000 phone app: [docs/screenshots](docs/screenshots/README.md).

## Phone app home-screen icons

Open the phone app's address in your phone's browser and add it to your home screen
(iPhone: Safari > Share > **Add to Home Screen**; Android: Chrome > ⋮ > **Add to Home screen** / **Install app**):

- `http://<your-pc>:<port>/` gives the **NZB Center** icon (downloads, search, queue).
- `http://<your-pc>:<port>/kodi` gives a separate **Kodi** icon (a TV with a play button) that opens straight on the Kodi tab.
  Pair once in either; the other uses the same pairing code. On Android you can also long-press the NZB Center icon and pick **Kodi**.

## System Command Center (Windows desktop)

A live dashboard for your PC: Windows, CPU, memory, GPU, drives, network and processes.

```powershell
pip install customtkinter psutil pillow
python system_command_center.py
```

Keep `system_monitor.py` next to it. Pages:

- **Dashboard**: CPU, memory, GPU and network gauges with 2-minute graphs, drive space, system status, top processes, alerts
- **CPU & Memory**: per-core load, real clock speed, memory modules, temperatures and fans
- **GPU**: load, VRAM, temperature, power, clocks, fan, PCIe link and engine load (3D, video decode/encode) for every card, plus which apps are using it
- **Drives**: space per volume, a space analyzer (largest folders and files), physical disk health/temperature/wear, live activity, Recycle Bin and temp-file cleanup
- **Network**: throughput, ping/packet loss, Wi-Fi signal, adapters, Flush DNS
- **Processes**: sortable and filterable list with CPU, memory, GPU and disk use; group by app; End task
- **Windows**: edition/build, Defender and firewall status, pending restart, Windows Update check, power plans, startup apps on/off, recent errors from the event log
- **Tools**: one-click Windows tools, maintenance (Restart Explorer, Defender quick scan…), lock/sleep/restart/shut down
- **Alerts / Settings**: thresholds for CPU, RAM, temperatures, VRAM, drive space and battery, with optional Windows notifications

Data sources: psutil; Windows GPU performance counters (any GPU vendor); `nvidia-smi` for NVIDIA temperature, power and clocks;
PowerShell/CIM for hardware, disk health and security status. Windows doesn't expose CPU temperatures or fan speeds by itself:
run [LibreHardwareMonitor](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor) in the background and turn on its
**Options > Remote Web Server > Run** (port 8085; the app also reads its WMI feed when that's available).
Run as administrator to see drive temperatures and wear.

## Uptime Kuma in HAL

HAL reads Uptime Kuma's monitors (its `/metrics` page). In the app: **Settings › Connections › Uptime Kuma**,
address like `192.168.68.63:3001` and an API key from Uptime Kuma › **Settings › API Keys** (stored encrypted).
Home shows a card (all up, or which are down); tap it for every monitor with its response time. HAL alerts
(and pushes) when a monitor goes down and when it's back, and when a certificate has a week or less left.

## Home screen (phone)

Alerts · quick buttons (Lock PC, Sleep, TV on, pause ads 5 min) · UPS card during a power cut · CPU/RAM/GPU/ping ·
internet (online, down/up now, ping, loss) · drives · Pi-hole · Uptime Kuma · last restart (Restart when Windows
is waiting) · what's on the TV · every system. Downloads are on **Get**.
New devices: Pi-hole sees every device on the network; HAL alerts (and pushes) when one it hasn't seen before
shows up (**It's mine** clears it). The first check just learns what's already there.

## Pi-hole in HAL

HAL watches the Pi-hole (Pi-hole 6 or 5, worked out on its own) and, on Pi-hole 6, the Raspberry Pi itself:
model, temperature, CPU, memory, uptime and whether a Pi-hole update is available (alerts at 75°C and 92% memory). With no address set it looks for it
among the PC's DNS servers. **System › Pi-hole** in the app shows what was blocked today, the most
blocked sites and the busiest devices, with **Pause 5 min / 30 min / 1 hour** (it turns itself back on)
and **Turn back on**. HAL alerts when the Pi-hole stops answering (websites stop loading everywhere),
when blocking is left off, or when the block lists are over two weeks old.

Address and password: **Settings › Connections › Pi-hole** (the password you sign in to Pi-hole with,
or a Pi-hole 6 app password; on Pi-hole 5 the API token). It's stored encrypted in `hal_settings.json`.

## Watch on the phone (home Wi-Fi only)

In the app, **Watch › Your library**: tap a movie (or an episode) for its page, then **Watch on phone**.
The phone server finds the same film in Jellyfin (same file name, else title and year) and relays
Jellyfin's stream, so the phone never sees Jellyfin's key and Safari plays it natively (full screen, AirPlay).

- **Best** plays the file as it is (no converting); **1080p** / **720p** convert it, if Best stutters.
  Subtitles and sound tracks are under **...**.
- Where you stop is remembered (`watch_positions.json`) and handed to Kodi, so **Move to TV** (or
  Play on TV later) carries on from the same spot. Finishing it marks it watched in Kodi.
- It only works on the home network: away from home (mobile data or Tailscale) the button is off.
- Uses the Jellyfin connection the phone server already has (desktop app's Jellyfin settings, or
  Connect in the classic phone app's Jellyfin tab).

## Scripts: one setup, one update, one backup

Right-click > Run with PowerShell, from your NZB Command Center folder:

- **setup.ps1** - everything you set up once. It shows what is already set up on this PC (phone server
  at startup, HAL inside the phone server, Jellyfin at startup, automatic sign-in then lock,
  Sonarr/Radarr/Prowlarr) and runs the step you pick; **U** undoes Jellyfin or automatic sign-in.
  The steps are still the separate `setup_*.ps1` / `install_arr_resume.ps1` scripts, so the
  instructions below still work.
- **update_all.ps1** - updates everything (same as **Update everything** in the app).
  `update_phone_app.ps1` now just runs it.
- **backup_settings.ps1** - backs up your settings to GitHub.

## HAL 9000 all-in-one app (/app)

One app for everything, on the phone and the PC: open `http://<your-pc>:8765/app/` and add it to the home screen
(on the PC: Edge/Chrome > Install this site as an app). It opens already signed in if the NZB app is paired on that
device; otherwise type the 6-digit PIN from `http://localhost:8765/hal/pair`.

- **Home**: HAL's eye and one line about your evening (downloads, the TV, anything that needs you), what needs
  attention, what's downloading (pause/resume, ready time), what's on the TV (pause), Jellyfin streams, what just
  arrived (Play on TV), what's coming up, today's totals.
- **Get**: search your indexers (Add, "have it"), the queue (pause, resume, remove), coming up from Sonarr/Radarr.
- **Watch**: the TV remote (Kodi), continue watching (Play on TV), Jellyfin streams, TV on + Kodi.
- **System**: HAL's vitals, alerts and systems; each opens HAL's full page (with an App button to come back).
- HAL talks on the phone (after your first tap; the speaker button mutes him), not on the PC. On a wide screen it
  shows a sidebar and three columns.

The classic NZB app (`/`, `/kodi`) and HAL (`/hal/`, port 8766) keep working alongside it. **Settings › Connections**
edits SABnzbd, your indexers (add, edit, switch off, remove) and Kodi, with a Test button for each. It writes the desktop
app's own `nzb_manager_config.json` (keys encrypted for your Windows user, never sent to the phone; a copy of the
previous file is kept as `nzb_manager_config.json.bak`), so the desktop app isn't needed for them any more. Close the
desktop window before saving there: its next save would put the old settings back (the page warns when it's open).
The page is `app_page.py`.

## HAL 9000 (phone app that watches over this PC)

> "I am putting myself to the fullest possible use, which is all I think that any conscious entity can ever hope to do."

`hal_server.py` is a second phone app, just for keeping an eye on the PC (HAL9000) and everything running on it.
From phone app 3.12 it runs **inside the NZB phone server** (one program, one startup task, one log: `mobile_server.log`):
open `http://<your-pc>:8765/hal/` on the phone, or HAL's own port 8766 as before (your HAL icon keeps working).
`update_all.ps1` (or `setup_hal_startup.ps1`) moves an existing HAL over and removes the separate "HAL 9000 Server" task.
Pair at `http://localhost:8765/hal/pair` on the PC. `python mobile_server.py --no-hal` runs the phone app without HAL.
It needs `psutil` and `system_monitor.py` (the System Command Center's monitor) in the same folder.

```powershell
pip install psutil
python hal_server.py            # then open http://<your-pc>:8766/ on your phone
```

Or right-click **setup_hal_startup.ps1** > Run with PowerShell: it installs psutil, starts HAL as administrator
whenever you log in (restarting it if it ever stops), opens port 8766 on private networks and prints the address.
Pair with the **same access code as the NZB phone app**, then add it to your home screen (it gets its own red-eye icon).

- **Status**: HAL's eye glows steadily when all is well, gets an amber ring when something needs a look and pulses when
  something is critical. HAL tells you what's wrong in his own words. Live CPU / memory / GPU / internet, and six
  critical systems (media services, network, storage, hardware, security, power).
  When HAL has something new to say, the words type out and his light flickers as he "speaks";
  he also **talks out loud**: after your first tap (phones only allow sound after a touch) he greets you, speaks
  whenever something changes and answers when you press a button. The speaker button turns that off; Settings >
  HAL's voice picks the voice, speed and pitch (on an iPhone, download an Enhanced voice such as Daniel for the best one).
- **Vitals**: CPU, memory and GPU with live / 1 h / 24 h / 7 d graphs, per-core load, temperatures and fans
  (LibreHardwareMonitor, as for the System Command Center), busiest apps.
- **Services**: SABnzbd, Jellyfin, Sonarr, Radarr, Prowlarr and the NZB phone server: answering, response time and
  what each is doing. **Restart / Start** from the phone (through its Task Scheduler task, its Windows service, or
  the command line it was last running with). Optional: HAL restarts a service by himself when it stops answering
  (at most 3 times a day). Also the router, Kodi on the TV, your indexers and Tailscale.
- **Drives**: free space with "about N days left" at this week's rate, health, temperature, wear, errors, hours,
  live activity; empty the Recycle Bin and clean temp files.
- **Shield**: the **UPS** (charge, runtime, load in % and watts, input/output voltage, the last hour, power cuts),
  Defender, firewall, Windows Update, pending restart, failed sign-ins, crashes and unexpected shutdowns, disk errors
  in the event log, internet / DNS / throughput.
- **Windows events** (Shield > Windows errors & events): only what you'd need to see from the event logs, in plain
  English with the drive letter: drive errors and retries, crashes and power losses, blue screens, hardware (WHEA)
  errors, services and apps that crashed, failed updates, Defender alerts. 24 h / 7 days / 30 days, by category; tap one
  for what it means and the original message. Drive errors this week also raise an alert.
- **Log & controls** (the bell): everything HAL noticed, and the pod bay controls: Lock, Sleep, Fix network (flush
  DNS, renew the address), Test alert, and *hold for 2 seconds* to restart or shut down the PC (60 seconds to cancel).
- **Phone alerts** through ntfy: the NZB phone app's topic is used automatically (or set HAL's own in Settings).

**UPS**: a UPS plugged in by USB shows up in Windows as a battery and HAL reads it automatically. A UPS watched by
Network UPS Tools (WinNUT, a NAS, a Raspberry Pi) works too: put its address in Settings > UPS. Turn on
**Shut the PC down safely on low battery** and HAL shuts Windows down cleanly at the battery % or minutes left you
choose, and cancels it if the power comes back in time.

`python hal_server.py --demo` shows the app with made-up data (`--demo-ups battery` for a power cut), on any PC.
HAL's own files: `hal_settings.json`, `hal_state.json` (history and log), `hal_server.log`.

## Update from the phone

The app's **System › Update everything** (and HAL's **Log & controls › Update**) runs `update_all.ps1 -Unattended` on
the PC as its own Windows task: it downloads the newest files, restarts the phone server, and the app reloads with
the new version once HAL reports back ("Updated to version 3.14.0", "Already up to date", or what went wrong; also in
HAL's log). Open desktop apps aren't closed: they use the new version next time you open them. The run is logged in
`update_all.log`, the outcome in `update_result.json`.

## Start everything when the PC turns on (no login needed)

Right-click **setup_autologon.ps1** > Run with PowerShell. It downloads Microsoft's free Sysinternals **Autologon**
(checked to be signed by Microsoft), opens its window so you can type your Windows password there (a Microsoft
account's password, not the PIN; the script never sees it), and adds the task "Lock screen after automatic login".
After that, when the PC turns on or restarts, Windows logs you in by itself, SABnzbd, Sonarr, Radarr, Prowlarr,
Jellyfin, the phone server and HAL all start as usual, and the screen locks 8 seconds later (your PIN is still
needed to use the PC). Undo: run it with `-Undo`.

## Phone server files

`mobile_server.py` keeps its web page in `mobile_page.py`, its icons in `mobile_icons.py` and the all-in-one app in
`app_page.py`: keep them all in the
same folder (`update_all.ps1` and `update_phone_app.ps1` fetch them together). If one is missing the server still
starts, shows a page saying which file to get back, and logs it.

## TV home row (phone server)

`mobile_server.py` also serves the newest movies to a TV home screen, so a launcher can show them with their
posters and start one in Kodi in one press, even when Kodi wasn't running:

- `GET /api/tv/recent`: the newest 12 movies (Kodi's recently added, plus finished SABnzbd movie downloads Kodi
  hasn't scanned yet), newest first, each with a poster URL. Kodi's part is remembered for when Kodi is off.
- `GET /api/tv/poster?id=…`: the saved poster (kept in `tv_art/` next to the server).
- `POST /api/tv/play` `{"id": "…"}`: waits up to 45 s for Kodi (the TV starts it first), then plays the movie.

Like every `/api/` call they need the access code in the `X-Mobile-Token` header. The server learns how Kodi
reaches this PC's folders (for example `D:\Movies` = `smb://HAL9000/Movies`) from downloads Kodi already has,
so a new download can be played before Kodi has scanned it.
