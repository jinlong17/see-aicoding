/* Public documentation only: render synthetic fixtures, never read a live API.
 * Requires Node.js, Playwright, and Chrome. Set PLAYWRIGHT_MODULE to a module
 * path if Playwright is not installed in the normal Node resolution path.
 * Run from any directory: node scripts/capture-docs.cjs
 */
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const root = path.resolve(__dirname, "..");
const staticRoot = path.join(root, "src/see_aicoding/web_static");
const output = path.join(root, "docs/images");
const snapshot = {
  schema_version: 3, stream_compact: true, simple_view: true,
  generated_at: 1788912000, refresh_interval: 3,
  system: {
    user: "demo", hostname: "local-machine", platform: "Demo system",
    logical_cpus: 12, physical_cpus: 8,
    cpu: { percent: 47, load_1: 2.4 },
    memory: { percent: 63, used_bytes: 20 * 1024 ** 3, total_bytes: 32 * 1024 ** 3 },
    swap: { total_bytes: 0 },
    gpu: { available: true, utilization_percent: 23, memory_used_bytes: 512 * 1024 ** 2, devices: [{ name: "Demo GPU" }] },
    disks: [{ is_system: true, percent: 54, used_bytes: 540 * 1024 ** 3, total_bytes: 1000 * 1024 ** 3, free_bytes: 460 * 1024 ** 3, mountpoint: "/" }],
    network: { download_bytes_per_s: 640 * 1024, upload_bytes_per_s: 128 * 1024 },
    process_summary: { total: 218, running: 176, threads: 1420 },
    history: { network_download_bytes_per_s: [1024 * 1024], network_upload_bytes_per_s: [512 * 1024] },
  },
};
const providers = [
  { id: "claude", manual_fallback: true, source: { kind: "manual_local" }, windows: [{ id: "five_hour", used_percent: 28 }, { id: "weekly", used_percent: 34 }] },
  { id: "chatgpt", manual_fallback: true, source: { kind: "manual_local" }, windows: [{ id: "five_hour", used_percent: 42 }, { id: "weekly", used_percent: 18 }] },
  { id: "cursor", status: "unavailable", windows: [] },
];

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: "chrome", headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1920, height: 1200 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    let preferences = {};
    // Intercept every request, including fonts: no live process or account data
    // and no external network are needed to build these screenshots.
    await page.route("**/*", async (route) => {
      const { pathname } = new URL(route.request().url());
      const files = { "/": ["index.html", "text/html"], "/static/app.css": ["app.css", "text/css"], "/static/app.js": ["app.js", "application/javascript"] };
      if (files[pathname]) {
        const [file, contentType] = files[pathname];
        return route.fulfill({ contentType, body: fs.readFileSync(path.join(staticRoot, file)) });
      }
      if (pathname === "/api/dashboard-preferences") {
        if (route.request().method() === "POST") preferences = route.request().postDataJSON().preferences;
        return route.fulfill({ json: { preferences, persisted: true } });
      }
      if (pathname === "/api/provider-usage") return route.fulfill({ json: { providers } });
      if (pathname === "/api/snapshot") return route.fulfill({ json: snapshot });
      if (pathname === "/events") return route.fulfill({ contentType: "text/event-stream", body: `retry: 60000\nevent: snapshot\ndata: ${JSON.stringify(snapshot)}\n\n` });
      return route.fulfill({ contentType: pathname.startsWith("/api/") ? "application/json" : "text/css", body: pathname.startsWith("/api/") ? "{}" : "" });
    });
    for (const style of ["tonearm", "vinyl", "engraved"]) {
      preferences = { dashboard_view: "simple", simple_style: style, simple_card_size: "medium", language: "zh-CN", theme: style === "engraved" ? "deep" : "light", font_size: "large" };
      await page.goto("http://127.0.0.1:8765/");
      await page.waitForFunction(() => document.querySelector("#hostLine")?.textContent.includes("demo@local-machine"));
      await page.evaluate(() => { stopEvents(); setConnection("演示数据"); });
      await page.waitForTimeout(200);
      assert.equal(await page.locator("[data-simple-metric-card]").count(), 9);
      assert.equal(await page.title(), "see-aicoding · 系统监视器");
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
      await page.screenshot({ path: path.join(output, `simple-${style}.png`), animations: "disabled" });
      const card = page.locator('[data-simple-metric-card="memory"]');
      await card.dblclick();
      assert(await page.locator(".simple-focus-dialog").isVisible());
      await page.keyboard.press("Escape");
      assert.equal(await page.locator(".simple-focus-dialog").count(), 0);
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
    assert.equal(errors.length, 0, JSON.stringify(errors));
    console.log("PASS: three synthetic previews, page identity, nine cards, desktop/mobile overflow, full-window entry/exit, console health.");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
