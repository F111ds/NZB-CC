# Trying Usenet streaming with AltMount

This test finds out whether streaming straight from your Usenet provider works well enough on **your**
provider and **your** connection, before anything in the real setup changes.

You test on the **PC first** (with VLC), and only then, if you like what you see, on the **TV** (Kodi).
During the test, SABnzbd, Sonarr, Radarr, Jellyfin and NZB-CC are **not touched**. To undo everything, you
close one window and delete one folder.

**Time:** about 20 minutes to set up, then a few evenings of watching.

---

## Step 1: Have these ready
- Your Usenet provider's **server address, port, username and password**. SABnzbd has them under
  *Config → Servers*.
- How many **connections** your provider plan allows (on your provider's account page), and how many
  SABnzbd uses (same SABnzbd page).
- **VLC** on the PC: free from videolan.org. It plays almost anything, including 4K, HEVC and DTS.
- An **.nzb file** for a movie. Download it from your indexer's website, as you would to add it to SABnzbd
  by hand. For the tests in step 7, have a few: a recent 1080p movie, a 4K movie, one released 3+ years ago
  and a TV episode.

## Step 2: Start AltMount
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
  The TV needs this later.

> If the script can't download, get the file ending in `_windows_amd64.zip` from
> https://github.com/kipsilabs/altmount/releases, unzip it into `C:\AltMount` and run the script again.

## Step 3: Create your login
AltMount's page (http://localhost:8090) asks you to create the admin account the first time. Do that.

## Step 4: Add your Usenet provider
In AltMount: **Configuration → Providers → add a provider**.
- Host, port (usually **563**), SSL **on**, username and password: the same as in SABnzbd.
- **Connections:** SABnzbd and AltMount **share your plan's limit**. For example, with 50 allowed and
  SABnzbd using 40, give AltMount **10**, or lower SABnzbd to 30 and give AltMount 20. Going over the limit
  causes login errors in both programs.
- Save, and use the test button if there is one.

While you test, **pause SABnzbd** (except for test 6 in step 7), so the two don't compete for bandwidth.

## Step 5: Note the WebDAV login
Still in **Configuration**, find the **WebDAV** section. It shows the username and password that VLC and
Kodi will use, `usenet` / `usenet` by default. You can change them; just use the same ones below.

## Step 6: Add a movie
1. In AltMount, open the **Queue** page and **upload** the .nzb file.
2. It should finish within **seconds to a minute**. Nothing big downloads: AltMount only reads the file
   list and checks a sample of the pieces.
3. If it **fails**, the reason is shown. Common ones: the release is *compressed or encrypted* (it can't be
   streamed, so pick another release), or *missing articles* (your provider no longer has all of it).

---

## Step 7: Test on the PC (VLC)

1. In AltMount, open the **Files** page. Open the folders (uploads without a category end up in `complete`,
   then a folder named after the movie) until you see the video file.
2. Note its path, for example:
   `/complete/Short.Circuit.1986.1080p/Short.Circuit.1986.1080p.mkv`
3. In **VLC**: *Media → Open Network Stream*, and enter `http://localhost:8090/webdav` followed by that path:
   ```
   http://localhost:8090/webdav/complete/Short.Circuit.1986.1080p/Short.Circuit.1986.1080p.mkv
   ```
   Copy and paste the path if it's long or has spaces. When VLC asks for a username and password, use the
   WebDAV ones from step 5.
4. Play it.

> AltMount's Files page also has a **Preview** button that plays in the browser. It's fine for a quick look,
> but browsers can't play many movie formats (HEVC, DTS), so use VLC for the real test.

Try each of these, preferably on different evenings:

| # | Try this | Look for |
|---|---|---|
| 1 | A recent 1080p movie | How long until it starts (target: under 10 seconds) |
| 2 | The same movie: jump to the middle, then near the end | Does it resume within a few seconds? |
| 3 | A **4K** movie (a big remux if you have one) | Smooth for 20+ minutes, no buffering |
| 4 | A movie released **3+ years ago** | Plays to the end, or stops at missing pieces? |
| 5 | A TV episode | Same as 1 |
| 6 | Something while SABnzbd is downloading | Does it stutter? (shows whether the connection split is right) |

If anything stalls, look at **AltMount's window** and its **Health/Queue** pages. They show the reason
(for example missing segments or provider errors).

**It's a pass if** most things start in seconds, seek quickly and play to the end. An occasional failure on
old releases is normal: even SABnzbd can only repair those with par2, and streaming can't.

If the PC test doesn't pass, stop here and remove it (see the end). Kodi was never touched.

---

## Step 8: Test on the TV (Kodi), only if the PC test passed

This adds AltMount to Kodi as a separate source. It stays **out of your Kodi library** and is removed with
one press.

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
4. When Kodi asks what the folder contains, leave it as **None**. This keeps the test out of your library.

To play: **Videos → Files → AltMount (test)**, open the folders (`complete`, then the movie) and play it.
Run tests 1 to 4 from step 7 again on the TV. This checks the PC-to-TV leg (your home network) too.

---

## Stopping and removing it

- **Stop:** close the AltMount window. Start it again with the same command as in step 2.
- **Remove:** stop it and delete `C:\AltMount`. If you did step 8, long-press *AltMount (test)* in Kodi
  → **Remove source**.
- Nothing else was changed. If you lowered SABnzbd's connections, set them back, and un-pause SABnzbd.

---

## If the test passes: what comes next (not part of the test)

To make streaming part of the real setup, we'd do these together, step by step:

1. **Radarr (then Sonarr) sends "watch tonight" movies to AltMount**: add AltMount as a second download client
   behind a tag, so normal downloads still go to SABnzbd.
2. **Choose how streamed titles reach Kodi and Jellyfin.** On Windows with Kodi on the TV, the likely choice is
   AltMount's `.strm` files, which point at AltMount's address on the network, rather than symlinks or a
   mounted drive. This needs checking on your PC first.
3. **Start AltMount automatically with Windows**, like SABnzbd.
4. **A Stream button in the phone app**, next to *Download*. Mock-ups come first, before any change.
