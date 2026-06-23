// capture-flows-hero.mjs — one-off README hero capture for docs/flows.html.
//
// Loads /flows.html in the headless software-GL chromium, settles on a flattering
// state (clustered/adaptive view, may_weekday slice, hour 08:00, dark basemap, panel
// visible), frames the Barcelona metro with inland nodes still in view, waits for the
// deck draw to settle, and writes a full-viewport PNG. The PNG is then downsized to
// ~1366px wide by the companion downsize step (PIL) so it matches the geobrowser hero.

import path from "node:path";
import { fileURLToPath } from "node:url";
import { startServer } from "./lib/server.mjs";
import { launch, waitForStableRender } from "./lib/browser.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, "..", "..", "docs", "screenshots", "flows_hero.raw.png");

// A camera tight on the Barcelona metro where clustering EXPANDS into a
// readable hub-and-spoke (sized super-nodes + flow lines) — the flowmap's
// strength. Pulled-back wide views collapse the clusters onto the coast and
// read as an empty dark map, so frame the hub.
const HERO_CAM = { longitude: 2.12, latitude: 41.44, zoom: 10.5 };

async function main() {
  const srv = await startServer();
  const { browser, close } = await launch();
  const context = await browser.newContext({
    viewport: { width: 1600, height: 1000 },
    deviceScaleFactor: 1,
  });
  const page = await context.newPage();

  const errors = [];
  page.on("pageerror", (e) => errors.push(String(e && e.stack ? e.stack : e)));

  const url = srv.url.replace(/\/$/, "") + "/flows.html";
  await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });

  // Wait until the flowmap app is fully ready (locations + default slice loaded).
  await page.waitForFunction(() => {
    const fm = window.__fm;
    return !!(fm && fm.deck && window.__fmReady);
  }, { timeout: 90000, polling: 250 });

  // Defaults are already the flattering state: clustering ON, adaptive scales,
  // may_weekday, hour 08:00, dark basemap. Just frame the metro.
  await page.evaluate(({ longitude, latitude, zoom }) => {
    const app = window.__fm;
    const vs = { longitude, latitude, zoom, pitch: 0, bearing: 0 };
    app._viewState = vs;
    app.deck.setProps({ initialViewState: vs, viewState: vs });
    if (app.map) app.map.jumpTo({ center: [longitude, latitude], zoom, pitch: 0, bearing: 0 });
    app._render();
  }, HERO_CAM);

  // Let the basemap tiles for the new view arrive, then settle the deck draw.
  await page.waitForLoadState("networkidle", { timeout: 30000 }).catch(() => {});
  await waitForStableRender(page, { settleMs: 600 }).catch(() => {});
  await page.waitForTimeout(900); // a touch more for the animated particles to populate

  await page.screenshot({ path: OUT, animations: "disabled" });

  await context.close();
  await close();
  await srv.stop();

  if (errors.length) {
    console.error("page errors during capture:", errors.join("\n"));
  }
  console.log("RAW_CAPTURE_OK", OUT);
}

main().catch((e) => { console.error(e); process.exit(1); });
