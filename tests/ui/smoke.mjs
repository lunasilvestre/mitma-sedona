// smoke.mjs — end-to-end proof for the geo-browser UI test harness.
//
// Stands up the static server over docs/, drives headless chromium with
// software WebGL to /explore.html, waits for the deck.gl map to render, then:
//   - captures a full-page screenshot AND a map-canvas-only screenshot to
//     tests/ui/proof/_smoke/,
//   - samples the canvas to decide map_rendered (non-blank),
//   - collects every console message, page error and failed request.
//
// Exit code 0 only if a real rendered-map screenshot was captured.
// Prints a JSON summary to stdout (consumable by scripts/CI).
//
// Run:  node tests/ui/smoke.mjs   (from repo root)
//   or: npm --prefix tests/ui run smoke

import path from "node:path";
import { fileURLToPath } from "node:url";
import { startServer } from "./lib/server.mjs";
import {
  launch,
  openExplore,
  waitForMapRendered,
  captureScreenshots,
  analyzeCanvas,
} from "./lib/browser.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROOF_DIR = path.join(HERE, "proof", "_smoke");

async function main() {
  const result = {
    smoke_works: false,
    map_rendered: false,
    smoke_screenshot_path: null,
    canvas_screenshot_path: null,
    gl_renderer: null,
    console_errors_seen: [],
    page_errors: [],
    failed_requests: [],
    canvas_analysis: null,
    blockers: [],
  };

  let server;
  let browser;
  let dispose;
  try {
    server = await startServer();
    console.error(`[smoke] server up at ${server.url} (pid ${server.pid})`);

    const launched = await launch();
    browser = launched.browser;

    const opened = await openExplore(browser, server.url);
    const { page, diagnostics, gl } = opened;
    dispose = opened.dispose;
    result.gl_renderer = gl.renderer;
    console.error(`[smoke] WebGL renderer: ${gl.renderer}`);

    await waitForMapRendered(page);
    console.error("[smoke] map render signal reached");

    const shots = await captureScreenshots(page, PROOF_DIR, "default");
    result.smoke_screenshot_path = shots.fullPagePath;
    result.canvas_screenshot_path = shots.canvasPath;

    const analysis = await analyzeCanvas(page);
    result.canvas_analysis = analysis;
    result.map_rendered = analysis.rendered;
    console.error(
      `[smoke] canvas: ${analysis.distinctColors} distinct colors -> rendered=${analysis.rendered}`
    );

    // Diagnostics snapshot.
    result.console_errors_seen = diagnostics.consoleErrors;
    result.page_errors = diagnostics.pageErrors;
    result.failed_requests = diagnostics.failedRequests;

    // smoke_works: a real screenshot of a RENDERED map.
    result.smoke_works = Boolean(result.smoke_screenshot_path) && result.map_rendered;
    if (!result.map_rendered) {
      result.blockers.push("map canvas appears blank: " + (analysis.reason || "unknown"));
    }
  } catch (err) {
    result.blockers.push(String(err && err.stack ? err.stack : err));
    console.error("[smoke] ERROR:", err);
  } finally {
    if (dispose) await dispose().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    if (server) await server.stop().catch(() => {});
  }

  // Machine-readable summary on stdout.
  console.log(JSON.stringify(result, null, 2));
  process.exit(result.smoke_works ? 0 : 1);
}

main();
