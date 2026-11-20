# Trying Usenet streaming with AltMount

The point of this test is to find out whether streaming straight from your Usenet provider works well
enough on **your** provider, **your** connection and **your** TV before anything in the real setup changes.

During the test, SABnzbd, Sonarr, Radarr, Jellyfin and NZB-CC are **not touched**. AltMount runs next to
them on its own port, and Kodi on the TV plays from it directly. If you don't like it, close one window
and delete one folder.

**Time:** about 20 minutes to set up, then a few evenings of watching.

---

## Part 1: Set it up (on the PC)

### 1. Have these ready
- Your Usenet provider's **server address, port, username and password**. SABnzbd has them under
  *Config → Servers* if you don't remember.
- How many **connections** your provider plan allows (on your provider's account page), and how many
  SABnzbd uses (same SABnzbd page).

### 2. Start AltMount
Open PowerShell in the NZB-CC folder and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\try_altmount.ps1
```

The script:
- downloads the latest AltMount for Windows from GitHub into `C:\AltMount` (first time only);
- starts it on port **8090** in its own window (SABnzbd already uses 8080, which is AltMount's normal port);
- opens AltMount's page in your browser.

Two Windows prompts may appear:
- **SmartScreen / Defender** ("Windows protected your PC"): AltMount isn't signed. Click *More info → Run anyway*.
- **Windows Firewall** ("Allow AltMount on networks?"): tick **Private networks** and click *Allow*.
  The TV can't reach it otherwise.

> If the script can't download, get the file ending in `_windows_amd64.zip` from
> https://github.com/kipsilabs/altmount/releases, unzip it into `C:\AltMount` and run the script again.

### 3. Create your login
AltMount's page (http://localhost:8090) asks you to create the admin account the first time. Do that.

### 4. Add your Usenet provider
In AltMount: **Configuration → Providers → add a provider**.
- Host, port (usually **563**), SSL **on**, username and password: the same as in SABnzbd.
- **Connections:** SABnzbd and AltMount **share your plan's limit**. For example, with 50 allowed and
  SABnzbd using 40, give AltMount **10**, or lower SABnzbd to 30 and give AltMount 20. Going over the limit
  causes login errors in both programs.
- Save, and use the test button if there is one.

> For a fair test, **pause SABnzbd** while you're watching something from AltMount, so the two don't compete
> for bandwidth.

### 5. Note the WebDAV login
Still in **Configuration**, find the **WebDAV** section. It shows the username and password that Kodi will
use, `usenet` / `usenet` by default. You can change them; just use the same ones in Kodi below.

---

## Part 2: Add a movie (on the PC)

1. Get an **.nzb file** for a movie: download it from your indexer's website, as you would to add it to
   SABnzbd by hand.
2. In AltMount, open the **Queue** page and **upload** the .nzb file.
3. It should finish within **seconds to a minute**. Nothing big downloads: AltMount only reads the file
   list and checks a sample of the pieces.
4. If it **fails**, the reason is shown. Common ones: the release is *compressed or encrypted* (it can't be
   streamed, so pick another release), or *missing articles* (your provider no longer has all of it).

---

## Part 3: Play it on the TV (Kodi)

Do this once:

1. On the TV, open **Kodi → Videos → Files → Add videos… → Browse → Add network location…**
2. Fill in:

   | Field | Value |
   |---|---|
   | Protocol | **WebDAV server (HTTP)** |
   | Server name | your PC's name (**HAL9000**, the same one Kodi uses for your movie folders) |
   | Remote path | **webdav** |
   | Port | **8090** |
   | Username / Password | the WebDAV ones from step 5 (default `usenet` / `usenet`) |

3. **OK → OK**, name the source **AltMount (test)**, and **OK**.
4. When Kodi asks what the folder contains, leave it as **None**. This keeps the test out of your Kodi library.

To play: **Videos → Files → AltMount (test)**, open the folders (uploads without a category end up in
`complete`), and play the movie.

---

## Part 4: The test

Try each of these, preferably on different evenings:

| # | Try this | Look for |
|---|---|---|
| 1 | A recent 1080p movie | How long until it starts (target: under 10 seconds) |
| 2 | The same movie: jump to the middle, then near the end | Does it resume within a few seconds? |
| 3 | A **4K** movie (a big remux if you have one) | Smooth for 20+ minutes, no buffering |
| 4 | A movie released **3+ years ago** | Plays to the end, or stops at missing pieces? |
| 5 | A TV episode | Same as 1 |
| 6 | Something while SABnzbd is downloading | Does the stream stutter? (shows whether the connection split is right) |

If anything stalls, look at **AltMount's window** and its **Health/Queue** pages. They show the reason
(for example missing segments or provider errors).

**It's a pass if** most things start in seconds, seek quickly and play to the end. An occasional failure on
old releases is normal: even SABnzbd can only repair those with par2, and streaming can't.

---

## Stopping and removing it

- **Stop:** close the AltMount window. Start it again with the same command.
- **Remove:** stop it, delete `C:\AltMount`, and in Kodi long-press *AltMount (test)* → **Remove source**.
- Nothing else was changed. If you lowered SABnzbd's connections, set them back.

---

## If the test passes: what comes next (not part of the test)

To make streaming part of the real setup, these would be done together, step by step:

1. **Radarr (then Sonarr) sends "watch tonight" movies to AltMount**: add AltMount as a second download client
   behind a tag, so normal downloads still go to SABnzbd.
2. **Choose how streamed titles reach Kodi and Jellyfin.** On Windows with Kodi on the TV, the likely choice is
   AltMount's `.strm` files, which point at AltMount's address on the network, rather than symlinks or a
   mounted drive. This needs checking on your PC first.
3. **Start AltMount automatically with Windows**, like SABnzbd.
4. **NZB-CC changes**, for example a *Stream now* button next to *Download* and a marker on streamed titles.
   Mock-ups come first, before any change.
