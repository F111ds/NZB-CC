mobile nzb command center

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

## HAL 9000 (phone app that watches over this PC)

> "I am putting myself to the fullest possible use, which is all I think that any conscious entity can ever hope to do."

`hal_server.py` is a second phone app, just for keeping an eye on the PC (HAL9000) and everything running on it.
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
  **tap the eye** to hear him say it out loud (your phone's voice, slowed and deepened).
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
- **Log & controls** (the bell): everything HAL noticed, and the pod bay controls: Lock, Sleep, Fix network (flush
  DNS, renew the address), Test alert, and *hold for 2 seconds* to restart or shut down the PC (60 seconds to cancel).
- **Phone alerts** through ntfy: the NZB phone app's topic is used automatically (or set HAL's own in Settings).

**UPS**: a UPS plugged in by USB shows up in Windows as a battery and HAL reads it automatically. A UPS watched by
Network UPS Tools (WinNUT, a NAS, a Raspberry Pi) works too: put its address in Settings > UPS. Turn on
**Shut the PC down safely on low battery** and HAL shuts Windows down cleanly at the battery % or minutes left you
choose, and cancels it if the power comes back in time.

`python hal_server.py --demo` shows the app with made-up data (`--demo-ups battery` for a power cut), on any PC.
HAL's own files: `hal_settings.json`, `hal_state.json` (history and log), `hal_server.log`.

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
