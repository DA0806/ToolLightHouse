/**
 * Optional developer loopback verification script using Playwright and synthetic WebRTC media.
 *
 * Requirements (optional developer tooling, not a runtime dependency):
 *   npm install --no-save playwright
 *   # or ensure playwright / chromium / edge is available in PATH or via BROWSER_PATH
 *
 * Usage:
 *   node frontend/scripts/review-loopback.mjs [baseUrl] [pairingKey]
 *
 * Environment variables:
 *   TOOLLIGHTHOUSE_TEST_URL          (default: http://127.0.0.1:8000)
 *   TOOLLIGHTHOUSE_TEST_PAIRING_KEY  (default: review-loopback)
 *   BROWSER_PATH                     (optional path to chrome / edge binary)
 *   SCREENSHOT_PATH                  (optional path for report screenshot)
 */

import os from "node:os";
import path from "node:path";

const baseUrl = process.env.TOOLLIGHTHOUSE_TEST_URL || process.argv[2] || "http://127.0.0.1:8000";
const pairingKey = process.env.TOOLLIGHTHOUSE_TEST_PAIRING_KEY || process.argv[3] || "review-loopback";
const executablePath = process.env.PLAYWRIGHT_CHROME_EXECUTABLE_PATH || process.env.BROWSER_PATH || undefined;
const screenshotPath = process.env.SCREENSHOT_PATH || path.join(os.tmpdir(), "toollighthouse-loopback-scan.png");

// Security guard: require strictly local mock backend to prevent accidental execution against production
const parsedUrl = new URL(baseUrl);
if (!["localhost", "127.0.0.1", "::1"].includes(parsedUrl.hostname)) {
  throw new Error(`review-loopback requires a local test instance; received non-localhost host: ${parsedUrl.hostname}`);
}

try {
  const statusRes = await fetch(`${baseUrl}/v1/status`);
  if (statusRes.ok) {
    const statusData = await statusRes.json();
    if (statusData.mode === "real" || (statusData.arduino && statusData.arduino.connected && !statusData.arduino.simulated)) {
      throw new Error("review-loopback is restricted to isolated mock backends; detected real mode or physical hardware");
    }
  }
} catch (err) {
  if (err.message && err.message.includes("review-loopback is restricted")) {
    throw err;
  }
  // Server might not be started yet or uses non-standard route; page.goto will report connection failure
}

// Optional Playwright loader
let playwright;
try {
  const { createRequire } = await import("node:module");
  const require = createRequire(import.meta.url);
  playwright = require("playwright");
} catch {
  try {
    playwright = await import("playwright");
  } catch {
    console.error(
      "Playwright is not installed. To run this optional verification, install it with:\n" +
      "  npm install --no-save playwright\n" +
      "or ensure Playwright is available in your node environment."
    );
    process.exit(1);
  }
}

const chromium = playwright.chromium || playwright.default?.chromium;
if (!chromium) {
  throw new Error("Failed to load Chromium from Playwright module");
}

function sanitizeHeaders(headers) {
  if (!headers) return {};
  const sanitized = {};
  for (const [k, v] of Object.entries(headers)) {
    if (/token|ticket|auth|cookie|key|secret/i.test(k)) {
      sanitized[k] = "[REDACTED]";
    } else {
      sanitized[k] = v;
    }
  }
  return sanitized;
}

function sanitizeUrl(rawUrl) {
  try {
    const u = new URL(rawUrl);
    if (u.hash.includes("token=")) {
      u.hash = "#token=[REDACTED]";
    }
    return u.toString();
  } catch {
    return "[MALFORMED_URL]";
  }
}

const streamRequests = [];
const inspectResponses = [];
const scanRequests = [];
const streamResponses = [];
const scanResponses = [];

const launchOptions = { headless: true };
if (executablePath) {
  launchOptions.executablePath = executablePath;
}

const browser = await chromium.launch(launchOptions);
let context;

try {
  context = await browser.newContext();
  const page = await context.newPage();

  page.on("request", (request) => {
    if (request.url().includes("/v1/stream/frame")) {
      streamRequests.push({ method: request.method(), headers: sanitizeHeaders(request.headers()) });
    }
    if (request.url().includes("/v1/analyze/scan")) {
      scanRequests.push({ method: request.method(), headers: sanitizeHeaders(request.headers()) });
    }
  });

  page.on("response", async (response) => {
    if (response.url().includes("/v1/stream/frame")) {
      streamResponses.push({ status: response.status(), body: await response.text().catch(() => "") });
    }
    if (response.url().includes("/v1/analyze/scan")) {
      scanResponses.push({ status: response.status(), body: await response.text().catch(() => "") });
    }
    if (response.url().includes("/v1/inspect")) {
      inspectResponses.push({ status: response.status(), body: await response.text().catch(() => "") });
    }
  });

  await page.goto(`${baseUrl}/`, { waitUntil: "networkidle" });
  await page.getByLabel("Clave de emparejamiento").fill(pairingKey);
  await page.getByRole("button", { name: "Verificar y conectar" }).click();
  await page.getByText("Conectado").waitFor({ timeout: 7000 });

  await page.getByRole("button", { name: "Vincular Celular (QR)" }).click();
  await page.getByText("Cámara Celular (WebRTC LAN)").waitFor({ timeout: 5000 });

  const dashboardText = await page.locator("body").innerText();
  const inviteMatch = dashboardText.match(/https?:\/\/[^\s]+#token=[^\s]+/);
  if (!inviteMatch) throw new Error("QR invite URL was not rendered");

  const remote = await context.newPage();
  await remote.addInitScript(() => {
    globalThis.__fakeMediaCalls = 0;
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: async () => {
          globalThis.__fakeMediaCalls += 1;
          const canvas = document.createElement("canvas");
          canvas.width = 900;
          canvas.height = 600;
          const ctx = canvas.getContext("2d");
          let frame = 0;
          const draw = () => {
            frame += 1;
            if (ctx) {
              ctx.fillStyle = frame % 2 ? "#6d28d9" : "#0f766e";
              ctx.fillRect(0, 0, canvas.width, canvas.height);
              ctx.fillStyle = "white";
              ctx.font = "28px sans-serif";
              ctx.fillText(`synthetic-${frame}`, 24, 120);
            }
            requestAnimationFrame(draw);
          };
          draw();
          return canvas.captureStream(15);
        },
      },
    });
  });

  await remote.goto(inviteMatch[0].replace(/^https:/, "http:"), { waitUntil: "networkidle" });
  await remote.locator("video").first().waitFor({ state: "visible", timeout: 10000 });
  await remote.waitForTimeout(6000);

  const remoteVideo = await remote.locator("video").first().evaluate((video) => ({
    readyState: video.readyState,
    width: video.videoWidth,
    height: video.videoHeight,
  }));
  if (remoteVideo.readyState < 2) {
    throw new Error(`Remote video failed to reach readyState >= 2 (got ${remoteVideo.readyState})`);
  }

  const closeModal = page.getByRole("button", { name: "Cerrar", exact: true });
  if (await closeModal.count()) await closeModal.click();

  await page.getByLabel("Fuente de Video").selectOption("remote_phone");
  await page.waitForTimeout(2500);

  const sourceValue = await page.getByLabel("Fuente de Video").inputValue();
  const controllerVideo = await page.locator("video").first().evaluate((video) => ({
    readyState: video.readyState,
    width: video.videoWidth,
    height: video.videoHeight,
  }));
  if (controllerVideo.readyState < 2) {
    throw new Error(`Controller video failed to reach readyState >= 2 (got ${controllerVideo.readyState})`);
  }

  const continuous = page.getByLabel("Continuo 2 FPS");
  await continuous.check();
  await page.waitForTimeout(1800);

  for (let attempt = 0; attempt < 20 && !streamRequests.length; attempt += 1) {
    await page.waitForTimeout(250);
  }

  const scanButton = page.getByRole("button", { name: "Analizar toma" });
  if (!(await scanButton.count())) {
    throw new Error("Remote capture scan button was not rendered");
  }
  await scanButton.waitFor({ state: "visible", timeout: 5000 });
  await scanButton.click();
  await page.waitForTimeout(5000);

  await page.screenshot({ path: screenshotPath, fullPage: true });
  const bodyText = await page.locator("body").innerText();
  const resultSignals = ["Resultados de la inspección", "Herramientas detectadas", "Inspección completada", "No se detectaron"];

  const hasStream200 = streamResponses.some((r) => r.status === 200);
  const hasScan200 = scanResponses.some((r) => r.status === 200);

  console.log(JSON.stringify({
    status: "success",
    inviteUrl: sanitizeUrl(inviteMatch[0]),
    remoteVideo,
    controllerVideo,
    fakeMediaCalls: await remote.evaluate(() => globalThis.__fakeMediaCalls),
    sourceValue,
    streamRequestsCount: streamRequests.length,
    streamResponsesCount: streamResponses.length,
    hasStream200,
    scanRequestsCount: scanRequests.length,
    scanResponsesCount: scanResponses.length,
    hasScan200,
    resultSignals: resultSignals.filter((signal) => bodyText.includes(signal)),
    screenshotPath,
  }, null, 2));

  if (!hasScan200) {
    throw new Error("Scan did not receive 200 response from backend");
  }
} finally {
  if (context) await context.close().catch(() => {});
  if (browser) await browser.close().catch(() => {});
}
