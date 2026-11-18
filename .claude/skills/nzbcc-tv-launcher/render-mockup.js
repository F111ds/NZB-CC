// Renders a mock-up HTML file to a PNG at TV resolution (1920x1080 from a 960x540 dp layout).
// Usage: node render-mockup.js <page.html> <out.png>   (copy sora.ttf next to the HTML first)
const { chromium } = require('playwright');
const path = require('path');
(async () => {
  const [page, out] = process.argv.slice(2);
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 960, height: 540 }, deviceScaleFactor: 2 });
  await p.goto('file://' + path.resolve(page));
  await p.waitForTimeout(400);
  await p.screenshot({ path: out });
  await b.close();
})();
