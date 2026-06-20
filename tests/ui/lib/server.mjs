// server.mjs — static file server helper for the geo-browser UI tests.
//
// Serves repo-root `docs/` over HTTP via `python3 -m http.server` on a FREE,
// auto-picked port. Guarantees clean start + teardown:
//   - we pick a free port ourselves (bind :0, read the OS-assigned port, release),
//   - we hand that exact port to python and track ONLY that child PID,
//   - stop() kills that one process group — never a broad pkill.
//
// Usage:
//   import { startServer } from "./lib/server.mjs";
//   const srv = await startServer();      // { url, port, stop }
//   ...                                    // srv.url === http://127.0.0.1:<port>
//   await srv.stop();

import { spawn } from "node:child_process";
import { createServer } from "node:net";
import { fileURLToPath } from "node:url";
import path from "node:path";
import http from "node:http";

const HERE = path.dirname(fileURLToPath(import.meta.url));
// tests/ui/lib -> repo root -> docs
export const DOCS_DIR = path.resolve(HERE, "..", "..", "..", "docs");
const HOST = "127.0.0.1";

// Ask the OS for a free TCP port by binding :0, then release it. There is a
// tiny race between release and python re-binding, but on loopback in a test
// context it is effectively never contended.
function pickFreePort() {
  return new Promise((resolve, reject) => {
    const probe = createServer();
    probe.unref();
    probe.on("error", reject);
    probe.listen(0, HOST, () => {
      const { port } = probe.address();
      probe.close(() => resolve(port));
    });
  });
}

// Poll the server root until it answers (python http.server is ready almost
// immediately, but we never assume).
function waitForReady(url, { timeoutMs = 15000, intervalMs = 100 } = {}) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const tick = () => {
      const req = http.get(url, (res) => {
        res.resume();
        resolve();
      });
      req.on("error", () => {
        if (Date.now() > deadline) {
          reject(new Error(`server at ${url} did not become ready in ${timeoutMs}ms`));
        } else {
          setTimeout(tick, intervalMs);
        }
      });
    };
    tick();
  });
}

/**
 * Start a static server over the repo's docs/ directory.
 * @param {{ port?: number, dir?: string }} [opts]
 * @returns {Promise<{ url: string, port: number, dir: string, stop: () => Promise<void> }>}
 */
export async function startServer(opts = {}) {
  const dir = opts.dir || DOCS_DIR;
  const port = opts.port || (await pickFreePort());

  // `-d <dir>` serves that directory without us having to chdir the parent.
  const child = spawn(
    "python3",
    ["-m", "http.server", String(port), "--bind", HOST, "--directory", dir],
    { stdio: ["ignore", "ignore", "pipe"], detached: false }
  );

  let stderr = "";
  child.stderr.on("data", (b) => { stderr += b.toString(); });

  // If python dies during startup, surface it instead of hanging the poll.
  const earlyExit = new Promise((_, reject) => {
    child.once("exit", (code) => {
      reject(new Error(`python http.server exited early (code ${code}). stderr: ${stderr.trim()}`));
    });
  });

  const url = `http://${HOST}:${port}/`;
  try {
    await Promise.race([waitForReady(url), earlyExit]);
  } catch (e) {
    try { child.kill("SIGKILL"); } catch { /* already gone */ }
    throw e;
  }

  let stopped = false;
  const stop = () =>
    new Promise((resolve) => {
      if (stopped || child.exitCode !== null || child.killed) {
        stopped = true;
        return resolve();
      }
      stopped = true;
      child.once("exit", () => resolve());
      // Kill only this child PID. Escalate to SIGKILL if it lingers.
      try { child.kill("SIGTERM"); } catch { return resolve(); }
      setTimeout(() => {
        if (child.exitCode === null && !child.killed) {
          try { child.kill("SIGKILL"); } catch { /* gone */ }
        }
      }, 2000);
    });

  return { url, port, dir, pid: child.pid, stop };
}
