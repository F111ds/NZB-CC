# Usenet streaming in the phone app: setting it up

This turns on the **▶ Stream** buttons in the phone app (Get tab). A streamed movie plays on the TV straight
from Usenet through AltMount, without downloading. Downloads (**+ Add**, SABnzbd, Sonarr, Radarr) and your
movies on the PC work exactly as before.

You need AltMount installed and working first (see `docs/altmount-streaming-test.md`).

---

## On the PC

### 1. Update NZB-CC
Tap **Update everything** in the phone app (or run `update_all.ps1`). This brings the new phone server and
app, plus `setup_altmount_startup.ps1`.

### 2. Turn on AltMount's streaming endpoint
The phone server talks to AltMount through its "Stremio" endpoint, which is off by default.
1. Open AltMount: **http://localhost:8090**
2. **Configuration → Stremio Integration**
3. Turn on **Enable Integration**, then click **Save Settings**.

You don't need Stremio itself, or anything else on that page.

### 3. Copy AltMount's API key
**Configuration → System → AltMount API Key**: copy it.

### 4. Connect the phone app to AltMount
In the phone app: **Settings (gear) → Connections**, scroll to **AltMount (Usenet streaming)**:
- **Address:** `http://localhost:8090`
- **API key:** paste the key from step 3
- Tap **Test**. It should say *"AltMount is answering, accepts the key and is ready to stream."*
  If it says the Stremio Integration is off, do step 2 again.
- Tap **Save**.

The **▶ Stream** buttons now appear on search results.

### 5. Let the TV reach AltMount
The TV plays the stream from the PC on port 8090, so Windows' firewall has to let it in. Open **PowerShell as
administrator** (Start → type PowerShell → right-click → Run as administrator) and run:
```powershell
New-NetFirewallRule -DisplayName "AltMount (Usenet streaming)" -Direction Inbound -Protocol TCP -LocalPort 8090 -Profile Private -Action Allow
```
This only allows your home network (Windows calls it "Private"). To check your network is set to Private:
```powershell
Get-NetConnectionProfile | Select-Object Name, NetworkCategory
```
If it says **Public**: Settings → Network & internet → your connection → set it to **Private network**.

### 6. Start AltMount with Windows
So streaming works without a PowerShell window open. In PowerShell (normal, not administrator), in the
NZB-CC folder:
```powershell
powershell -ExecutionPolicy Bypass -File .\setup_altmount_startup.ps1
```
It stops the AltMount window if one is open, makes a task that starts AltMount hidden at every logon (and
restarts it if it stops), and starts it now. To undo it: add `-Undo` to the same command.

---

## On the TV (Kodi)

**There's nothing you have to change.** The phone app already controls Kodi ("Play on TV"), and a stream uses
the same connection. Your Kodi library, sources and movie folders are not touched: streamed movies don't go into
the library. They play directly, and the app lists them under **Watch → Streamed lately**.

To be sure, check (it's almost certainly already like this, because Play on TV works):
- Kodi → **Settings (gear) → Services → Control**:
  - **Allow remote control via HTTP:** on
  - **Allow remote control from applications on other systems:** on

**Only if a stream stutters on the TV** (and the same movie plays smoothly on the PC in VLC): give Kodi a bigger
buffer for network streams. On Kodi 21, if your Kodi shows these:
1. Kodi → **Settings → Services**, and switch the settings level (bottom left) to **Expert**.
2. **Caching:** set **Buffer mode** to *Buffer all internet filesystems*, **Memory size** to *256 MB*,
   **Read factor** to *4x*.

---

## Using it

1. Phone app → **Get** → search for a movie.
2. Tap **▶ Stream** on a result. *Getting it ready* takes under a minute: nothing downloads.
3. **📺 Play on TV.** If the TV is off, it turns it on and opens Kodi first.

- **Blue ☁ USENET** label = streaming from Usenet. **Grey 🖥 ON YOUR PC** = a movie from your folders.
- **Can't stream this one** (compressed or incomplete releases): it offers another release, or *Download it
  instead* (SABnzbd, as usual).
- **Want to keep it?** *Download a copy too* on the Ready screen sends it to SABnzbd as well.
- **Watch on phone** opens the stream in **VLC** on the phone (install VLC from the App Store first).
- HAL says it too: *"You're watching Glass Meridian, streaming from Usenet."*

## If something doesn't work

| What you see | What to do |
|---|---|
| No Stream buttons | Settings → Connections → AltMount: fill in the address and key, Save. |
| "AltMount isn't answering" | AltMount isn't running. Run step 6, or start it with `try_altmount.ps1`. |
| "Stremio Integration is switched off" | Step 2. |
| "didn't accept the API key" | Copy the key again (step 3) into Connections. |
| Play on TV: Kodi shows an error or nothing plays | The firewall (step 5), and check the network is Private. |
| Streams stutter on the TV only | The Kodi buffer settings above. |
