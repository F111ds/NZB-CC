# HAL 9000: Siri and a home-screen widget

Both talk to the phone server on your PC, so they work at home (or anywhere Tailscale is on).
You need two things:

- **Your PC's address**: the one you open the app with, without `/app` at the end,
  for example `http://192.168.68.60:8765`.
- **Your access code**: on the PC, open `http://localhost:8765/hal/pair`. It's at the bottom, after
  "Or type the full access code". Never share it or post it anywhere.

## "Hey Siri, ask HAL"

One shortcut handles everything. You say "Hey Siri, ask HAL", then ask your question.

1. Open the **Shortcuts** app, tap **+**.
2. Add **Dictate Text**. Set *Stop Listening* to **After Pause**.
3. Add **Get Contents of URL**:
   - URL: `http://YOUR-PC:8765/hal/api/siri`
   - Method: **POST**
   - Headers: add `X-Mobile-Token`, value: your access code
   - Request Body: **JSON**, add a Text field `q`, value: the **Dictated Text** variable
4. Add **Get Dictionary Value**: get **Value** for key `say` in **Contents of URL**.
5. Add **Speak Text**: the **Dictionary Value**.
6. Tap the name at the top and call it **Ask HAL**.

Things you can say after "Hey Siri, ask HAL":

| Say | HAL |
|---|---|
| "Play Glass Meridian" | Finds it in your Kodi library and plays it on the TV, picking up where you left off |
| "Play Night Harbor" | A show: plays the next episode you haven't watched |
| "Turn on the TV and play Deep Field" | Turns the TV on and opens Kodi first, then plays it (HAL also does this by himself when the TV is off) |
| "Play Glass Meridian from the start" | From the beginning instead of where you stopped |
| "Pause" / "Resume" / "Stop" | What's playing in Kodi |
| "Skip back" / "Skip ahead" | 15 seconds back, 30 forward |
| "Turn it up" / "Turn it down" / "Turn it up by 5" / "Mute" | The TV's own volume, in any app |
| "Open YouTube" / "Open Netflix" / "Open Prime" | Opens the app on the TV (turning the TV on first if it's off) |
| "Play cat videos on YouTube" / "Search YouTube for …" | YouTube with the search results |
| "Watch Stranger Things on Netflix" / "Play The Office on Prime" | Searches Netflix or Prime for it, the way `try-tv-search.ps1` found works on your TV (run it once, see below). Pick it with the remote |
| "What's on?" | The movie in Kodi, the video on YouTube, the show on Netflix or Prime, or the games console |
| "What's new?" | What SABnzbd finished downloading today |
| "How's everything?" | A summary: alerts, downloads, ads blocked, drives |
| "Pause the ads" / "Pause the ads for 30 minutes" | Pauses Pi-hole (5, 30 or 60 minutes) |
| "Turn the ads back on" | Resumes Pi-hole |
| "Lock the PC" / "Put the PC to sleep" | Does it |
| "Turn on the TV" | Turns the TV on and opens Kodi |
| "What's downloading?" | SABnzbd's queue |
| "How hot is it?" | CPU, graphics card and Raspberry Pi temperatures |
| "How are the drives?" | How full each drive is |
| "How's the internet?" | Online or not, and the ping |
| "Is anything down?" | Uptime Kuma's monitors |
| "Give me my briefing" / "How was my week?" | The morning or weekly briefing |
| "What's my record?" | Lifetime ads blocked, data downloaded, days watching |
| "Open the pod bay doors" | Try it |
| "Sing" | Try that too |

Restart and shutdown are refused from Siri on purpose: use the app, where you can change your mind.

A quicker one, "Hey Siri, how's HAL": the same shortcut **without** step 2, with `q` set to the
text `status`, named **How's HAL**.

The same trick makes one-step commands for the things you say most, so there's no waiting for the beep:

| Shortcut name ("Hey Siri, …") | `q` text |
|---|---|
| HAL pause | `pause` |
| HAL resume | `resume` |
| HAL louder | `turn it up` |
| HAL quieter | `turn it down` |
| HAL mute | `mute` |
| What's on | `what's on` |

If HAL can't tell which title you meant, he plays the closest match and says which one. Titles are matched
without "the", punctuation or capital letters, so "the quiet sun" and "Quiet Sun" both work; saying the year
("Night Harbor 2024") picks between a movie and a show with the same name.

## Searching Netflix and Prime: run the test once

TV apps differ in which kind of search request they act on (some accept one and ignore it), so HAL needs to
know what works on your TV. On the PC, with the TV on and in view, run once:

    powershell -ExecutionPolicy Bypass -File .\tv-launcher\try-tv-search.ps1

It tries each way in turn and asks "did the TV show results?". The first one you say yes to is saved in
`tv_search_methods.json`, and HAL uses it at once. Until then, "search Netflix for …" just opens Netflix.
`-App netflix` tests only Netflix; `-Reset` forgets what was saved.

## Home-screen widget

1. Install **Scriptable** (free) from the App Store.
2. In Scriptable tap **+**, paste everything from [`extras/hal_widget.js`](../extras/hal_widget.js),
   change `ADDRESS` at the top to your PC's address, and name it **HAL 9000**.
3. Run it once (▶). It asks for the access code and keeps it in the iPhone's keychain, not in the
   script.
4. On the home screen: long-press, **+**, **Scriptable**, choose the small or medium size, add it.
   Long-press the new widget, **Edit Widget**, Script: **HAL 9000**.

The widget shows HAL's eye, which gets an amber or red ring when something needs you, along with
what HAL is saying and, on the medium size, CPU, memory, the fullest drive, ads blocked and
Uptime Kuma. Tap it to open the app. It refreshes about every 5 minutes, or whenever iOS allows.

## Morning briefing

In the app: **gear › HAL: name, alerts, UPS, voice › Briefings**. Pick a time (blank turns it off)
and a day for the weekly summary. It's sent like HAL's other phone alerts (ntfy), and also goes
into HAL's log.
