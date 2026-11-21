---
name: hal-style
description: >-
  The HAL 9000 look for NZB-CC: colours, type, cards, HAL's eye, gauges, graphs and screen sizes shared by
  the phone app (app_page.py), HAL's TV page (hal_tv_page.py), HAL's own page (hal_server.py) and the TV
  launcher (tv-launcher, Java). Use it whenever you design, mock up, restyle or review any screen of this
  project, add a card, graph, icon or page, or change how something looks on the phone or the TV.
---

# HAL style guide

One look on every screen: a dark red room lit by HAL's eye. Glass cards with thin red edges, bright readings
in mono type, calm motion. Only HAL's eye moves all the time. The phone and the TV must look like the same app.

## The owner's rules (CLAUDE.md, these come first)

- **Mock-up first.** Any change the owner can see or tap (layout, screens, buttons, wording, new pages) needs
  a mock-up and the owner's OK before code changes. Invisible changes (fixes, server work) don't; say
  what changed afterwards.
- **Send mock-ups and screenshots as PNG images in the chat**, one image per screen. Phone at 390x844,
  deviceScaleFactor 2; TV at 960x540, deviceScaleFactor 2 (1920x1080 out). The owner views them on a
  phone, where artifact links don't load.
- Mock up on a copy (inject CSS/DOM with Playwright into the real page served by the demo server:
  `python3 hal_server.py --demo --host 127.0.0.1 --port 8797`), never by editing the repo first.
- Demo data only in pictures: no real poster art, keys, addresses or the access code.

## Screen sizes

| Surface | Layout size | How it reaches the device |
|---|---|---|
| Phone app / HAL page | 390x844 points (iPhone) | drawn at 3x by the phone |
| HAL's TV page | 960x540 stage (`#stage`) | scaled to fit, up to 4x (4K); everything is vector or CSS, so it stays sharp |
| TV launcher home | Android dp | native; wallpapers up to 4K |

Design TV text for the couch: nothing under 9.5px on the 960x540 stage, main numbers 30px or more.

## Colour

Surfaces (dark only; there is no light mode):

| Token | Value | Use |
|---|---|---|
| room | `#0a0405` (html), TV room gradient, phone ember shader | page background |
| card | `linear-gradient(170deg, rgba(60,16,14,.80), rgba(18,6,7,.90))` | every card, phone and TV |
| card edge | `1px solid rgba(255,90,60,.35-.38)` + `0 0 24-26px rgba(255,40,20,.16-.18)` | the red edge and glow |
| divider | `rgba(255,255,255,.08-.1)` | rows, column splits |
| glow rule | `linear-gradient(90deg, transparent, rgba(255,60,30,.85), transparent)` + `0 0 8px rgba(255,40,20,.8)` | under HAL's words |

Ink (text never wears a series colour):

| Token | Value |
|---|---|
| primary | `#fff` / `#F4F4F5` (big numbers), `#ffe9e0` (HAL's words) |
| secondary | `rgba(255,235,228,.75-.8)` (headings, labels) |
| muted | `rgba(255,230,220,.6)` / `--muted #9A8A86` |

Accent: HAL red `#FF2A1F` / `#ff3a1e` (cursor, active tab, underline), orange `#FF6A4D` / `#FF7A45`.

Status (reserved: never a series colour, always with a word or icon, never colour alone):

| State | Colour | Text that goes with it |
|---|---|---|
| ok | `#7cf5b5` (TV) / `#34D399` (phone) | `● OK`, `● ALL UP`, `● ONLINE` |
| warning | `#ffc46b` / `#FBBF24` | `● CHECK` |
| critical | `#ff7a6b` / `#ff6a4d` | `● PROBLEM` |
| info | `#9cc8ff` / `#60A5FA` | |

Data colours (identity, fixed order, same meaning on every screen):

| Series | Colour | Where |
|---|---|---|
| CPU | orange `#ffd27a -> #ff4b2b` | gauge, CPU graph `#ff9a5c` |
| Temperature | red `#ff9a3c -> #e11d48` | gauge |
| Memory | teal `#9ff5d0 -> #2dd4bf` | gauge |
| Network download | teal bars `#5ff2e0 -> #16a99a`, arrow `#4aa8ff` | network card |
| Upload / ping arrows | `#5eea9b` / `#b07aff` | network card labels |
| DNS asked / blocked | amber `#ffc46b` / red `#ff3a1e` | Pi DNS bars |
| Drives | `#ff9a3c -> #ff4b2b`, nearly full `#ffc46b -> #ffb020` | drive bars |

Check any new set of data colours with the dataviz skill's validator against the card surface
(`--mode dark --surface "#240a09"`). Bright neon on dark fails its lightness band by design; CVD
separation must pass. Never put red and green side by side as the only difference.

## Type

- Body: `"Plus Jakarta Sans", -apple-system, system-ui, sans-serif`.
- Readings, labels, headings: `"JetBrains Mono", ui-monospace, monospace`.
- Card headings: mono 700, 10-12px, letter-spacing .14-.16em, CAPITALS, secondary ink, with a 15px line icon
  in front (`#ddd`, stroke 1.8). Status on the right in its status colour.
- Big numbers: Plus Jakarta 800, letter-spacing -.03em, line-height 1; units smaller beside them.
- HAL's greeting: mono 500, letter-spacing .32-.34em, CAPITALS ("GOOD AFTERNOON, DAVE.").
- HAL's words: Plus Jakarta 300 italic, 17px TV / 19px phone, in curly quotes, ending in the red cursor
  (2px wide, `#ff3a1e`). The greeting is never repeated inside the line.

## HAL's eye

The same eye everywhere (phone hero, TV page, launcher `HalEyeDrawable`):

- Chrome rim: `conic-gradient(from 210deg, #f4f6f8, #7c828a 15%, #2a2d32 30%, #d8dce0 48%, #5c6168 62%, #1e2024 78%, #e9ecef 92%, #f4f6f8)`
  inside a black ring (`radial-gradient(circle closest-side, #050505 0 84%, transparent 85%)`).
- Lens: `radial-gradient(#fff3c4 0, #ffb060 5%, #ff4a1a 14%, #d4140a 32%, #6a0603 58%, #140000 85%, #000)`,
  inset 16%, inner shadow `inset 0 0 14px 4px rgba(0,0,0,.95)`, glint `ellipse 38% 20% at 38% 22%, rgba(255,255,255,.35)`.
- Glow `0 0 60-64px 16-18px rgba(255,30,10,.45)`; three horizontal light lines through it (`.hflare`, the
  outer two at 30% opacity).
- Gaze: move only the lens light (`--gx`, `--gy`), about 13x10px on the TV, 9x7px on the phone. He blinks,
  glances, looks at what he is talking about and brightens on the sounds while he talks.
- Around the eye: the CPU / TEMP / MEM gauges (rings r 128/116/104 in a 300 box, labels top-left in mono),
  60-72 ticks. The radar sweep is the alternative the owner has used; don't show both.

## Cards and layout

- Radius 20px (cards), 27px (the download strip), 14-16px (small chips). Padding 15-17px.
- Phone: one column of cards, capsule tab bar (house, Get, Watch, System) with dividers and a red underline
  on the active tab; the Pi strip under it.
- TV page: four cards round the eye (PC and Network left, Uptime Kuma and Raspberry Pi right), download strip
  at the bottom, button hints and clock under it. Details pages: tabs top right, two rows of cards.
- Even spacing: equal outer margins, the eye centred between the top and the content below it.

## Motion (calm on purpose)

- Moves all the time: HAL's eye only (gaze, blink, talk glow) and its ring when one is shown.
- Graphs and bars are still. Draw them again only when their data changes. No waveforms, particles or embers.
- Boot: the eye scales in, cards unfold from it, once.
- Respect `prefers-reduced-motion` on the phone.

## Graphs (dataviz rules that apply here)

- Real data only. Never draw a made-up line as decoration; if there is no history, show the number alone.
- One measure per graph and one axis. If a legend names two series, both must be drawn.
- Thin marks: 1.3-1.6px lines with a soft fill under them, 2px gaps between bars, rounded bar tops.
- Text beside a graph uses ink colours; the coloured mark carries the identity.
- The same thing gets the same colour on every screen (allowed / blocked, up / down).

## Icons

Inline SVG line icons, 24 viewBox, `stroke="currentColor"`, width 1.8 (arrows 2.4), round caps and joins.
No CSS mask icons (iPhone Safari draws them blank when set through a custom property; use direct
`mask-image` or inline SVG).

## Before you call a screen done

1. Render it with demo data at the sizes above and look at it (overflow, cut-off text, collisions).
2. Compare it with the matching phone or TV screen: same colours, type and eye.
3. Send the PNGs, wait for the owner's OK, then build.
