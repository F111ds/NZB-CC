// HAL 9000 home-screen widget, for the free "Scriptable" app on iPhone.
// Setup: docs/siri-and-widget.md. Shows HAL's eye (ringed amber or red when something's wrong),
// what he's saying, and a few live readings. Tap it to open the app.
//
// 1. Change ADDRESS to the address you open the app with (without /app at the end).
// 2. Run this script once inside Scriptable: it asks for your access code and keeps it in the
//    iPhone's keychain (it is never written into this script).

const ADDRESS = "http://192.168.68.60:8765";   // <- your PC, the same address the app uses
const KEY = "hal9000_access_code";

async function accessCode() {
  if (Keychain.contains(KEY)) return Keychain.get(KEY);
  if (config.runsInWidget) return null;
  const a = new Alert();
  a.title = "HAL 9000";
  a.message = "Paste the access code (the same one the phone app uses). It stays in this iPhone's keychain.";
  a.addSecureTextField("Access code", "");
  a.addAction("Save");
  a.addCancelAction("Cancel");
  if ((await a.presentAlert()) === -1) return null;
  const code = a.textFieldValue(0).replace(/[^A-Za-z0-9_\-]/g, "");
  if (code) Keychain.set(KEY, code);
  return code || null;
}

async function load(code) {
  const r = new Request(ADDRESS + "/hal/api/brief");
  r.headers = { "X-Mobile-Token": code };
  r.timeoutInterval = 8;
  return await r.loadJSON();
}

function eye(level, size) {
  const d = new DrawContext();
  d.size = new Size(size, size);
  d.opaque = false;
  d.respectScreenScale = true;
  const ring = level === "critical" ? new Color("#FF2A1F") : level === "warning" ? new Color("#FBBF24") : new Color("#3b3b42");
  const circle = (inset, color) => { d.setFillColor(color); d.fillEllipse(new Rect(inset, inset, size - 2 * inset, size - 2 * inset)); };
  circle(0, ring);
  circle(size * 0.04, new Color("#0d0d0f"));
  circle(size * 0.08, new Color("#2a2a2e"));
  circle(size * 0.16, new Color("#140000"));
  circle(size * 0.22, new Color("#5A0402"));
  circle(size * 0.28, new Color("#C80E08"));
  circle(size * 0.36, new Color("#FF3B1F"));
  circle(size * 0.44, new Color("#FFC14A"));
  circle(size * 0.47, new Color("#FFF6C8"));
  return d.getImage();
}

function addLine(stack, label, value, color) {
  const row = stack.addStack();
  const l = row.addText(label);
  l.font = Font.mediumMonospacedSystemFont(10);
  l.textColor = new Color("#71717A");
  row.addSpacer();
  const v = row.addText(value);
  v.font = Font.semiboldMonospacedSystemFont(11);
  v.textColor = color || new Color("#D4D4D8");
}

async function build() {
  const w = new ListWidget();
  w.backgroundColor = new Color("#050506");
  w.url = ADDRESS + "/app/";
  w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000);
  const small = config.widgetFamily === "small";
  const code = await accessCode();
  let d = null, err = "";
  if (!code) err = "Run the script in Scriptable once to add the access code.";
  else {
    try { d = await load(code); } catch (e) { err = "I can't reach the PC. (At home, or on Tailscale?)"; }
    if (d && d.error) { err = d.error; d = null; }
  }
  const top = w.addStack();
  top.centerAlignContent();
  const img = top.addImage(eye(d ? d.level : "", small ? 46 : 52));
  img.imageSize = new Size(small ? 46 : 52, small ? 46 : 52);
  top.addSpacer(10);
  const t = top.addText(d ? (d.issues ? d.issues + (d.issues > 1 ? " things" : " thing") : "All fine") : "HAL 9000");
  t.font = Font.boldSystemFont(small ? 15 : 17);
  t.textColor = d && d.level === "critical" ? new Color("#FF6A4D") : d && d.level === "warning" ? new Color("#FBBF24") : Color.white();
  w.addSpacer(8);
  const q = w.addText(d ? "“" + d.quote + "”" : err);
  q.font = Font.italicSystemFont(small ? 11 : 12);
  q.textColor = new Color("#A1A1AA");
  q.lineLimit = small ? 3 : 2;
  if (d && !small) {
    w.addSpacer(8);
    const s = w.addStack();
    s.layoutHorizontally();
    const a = s.addStack(); a.layoutVertically();
    s.addSpacer(16);
    const b = s.addStack(); b.layoutVertically();
    addLine(a, "CPU ", d.cpu == null ? "—" : Math.round(d.cpu) + "%");
    addLine(a, "RAM ", d.ram == null ? "—" : Math.round(d.ram) + "%");
    if (d.fullest) addLine(b, d.fullest.mount + " ", Math.round(d.fullest.pct) + "%", d.fullest.pct >= 90 ? new Color("#FBBF24") : null);
    if (d.ads != null) addLine(b, "ADS ", Number(d.ads).toLocaleString());
    if (d.kuma_down != null) addLine(a, "KUMA ", d.kuma_down ? d.kuma_down + " down" : "all up", d.kuma_down ? new Color("#FF6A4D") : new Color("#34D399"));
  }
  return w;
}

const widget = await build();
if (config.runsInWidget) Script.setWidget(widget);
else await widget.presentMedium();
Script.complete();
