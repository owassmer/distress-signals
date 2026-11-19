// Browser regression for the published example run (November 19, 2026).
// With Playwright 1.45+ and its Chromium installed, run:
//   node tests/example-replay.cjs /path/to/exported/index.html
// Set CHROME_BIN to use an existing Chromium executable.
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

async function testReplay(page, url) {
  await page.goto(url + "#step/9");
  const log = page.locator("#v-research");
  const play = log.locator("#pp");
  const time = log.locator("#ptime");
  assert.match(await time.innerText(), /^3:12\b/);
  await log.getByRole("button", { name: "2×", exact: true }).click();
  await play.click();
  const firstEntry = await log.locator("#s0").elementHandle();

  // The old player discarded the first ten entries and changed the URL at 3:24.
  for (let elapsed = 0; elapsed < 6000 && !(await time.innerText()).startsWith("3:24"); elapsed += 100) {
    await page.clock.runFor(100);
  }
  assert.match(await time.innerText(), /^3:24\b/);
  assert.equal(await firstEntry.evaluate(node => node.isConnected), true);
  assert.equal(await log.locator("#s9").isVisible(), true);
  assert.equal(page.url(), url + "#step/9");
  await play.click();
}

async function testActivity(page, url) {
  const nav = page.getByRole("navigation", { name: "Run navigation" });
  await nav.getByRole("link", { name: "Research activity" }).click();
  const activity = page.locator("#v-activity");
  await activity.waitFor();
  assert.equal(page.url(), url + "#activity");
  const seek = activity.getByRole("slider");
  const toggle = activity.locator("#activity-toggle");
  const distances = [];
  for (const speed of [1, 2, 4]) {
    await seek.press("Home");
    const button = activity.getByRole("button", { name: speed + "×", exact: true });
    await button.click();
    assert.equal(await button.getAttribute("aria-pressed"), "true");
    await toggle.click();
    await page.clock.runFor(5000);
    await toggle.click();
    distances.push(Number(await seek.inputValue()));
  }
  assert.ok(distances[0] > 0);
  assert.equal(distances[1], 2 * distances[0]);
  assert.equal(distances[2], 4 * distances[0]);

  // The first case was saved at 13:11 during the entry that began at 12:44.
  const firstSave = activity.locator('a[href^="#found/"]').first();
  const bookmark = await firstSave.getAttribute("href");
  await firstSave.click();
  const log = page.locator("#v-research");
  await log.waitFor();
  assert.equal(page.url(), url + bookmark);
  const time = log.locator("#ptime");
  assert.match(await time.innerText(), /^13:11\b/);
  await log.locator("#pp").click();
  assert.match(await time.innerText(), /^13:11\b/);
}

async function main() {
  if (process.argv.length !== 3) throw new Error("Usage: node tests/example-replay.cjs /path/to/exported/index.html");
  const { chromium } = require("playwright");
  const browser = await chromium.launch({ executablePath: process.env.CHROME_BIN });
  try {
    const page = await browser.newPage({ reducedMotion: "reduce" });
    const url = pathToFileURL(path.resolve(process.argv[2])).href;
    await page.clock.install({ time: new Date("2026-11-19T12:00:00Z") });
    await page.clock.pauseAt(new Date("2026-11-19T12:00:01Z"));
    await testReplay(page, url);
    await testActivity(page, url);
    console.log("Transcript continuity, saved-finding playback and research activity passed.");
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
