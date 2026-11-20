// Browser regression for the published example run (November 19, 2026).
// With Playwright 1.45+ and its Chromium installed, run:
//   node tests/example-replay.cjs /path/to/exported/index.html
// Set CHROME_BIN to use an existing Chromium executable.
const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");

async function testSearchBriefAndReplay(page, url) {
  await page.goto(url);
  const overview = page.locator("#v-findings");
  const brief = overview.locator("details").filter({ has: page.locator("summary", { hasText: "Search brief" }) });
  const summary = brief.locator("summary");
  assert.equal(await brief.evaluate(element => element.open), false);
  await summary.press("Enter");
  const assignment = "Find operating companies where a material share of revenue depends on the outcome of a specific upcoming event outside the company's control.";
  assert.ok((await brief.innerText()).replace(/\s+/g, " ").includes(assignment));
  await summary.press("Enter");
  const replay = overview.getByRole("button", { name: /^Replay search\b/i })
    .or(overview.getByRole("link", { name: /^Replay search\b/i }));
  assert.equal((await replay.innerText()).trim(), "Replay search · 0:59");
  await replay.click();
  const activity = overview.locator("#activity");
  const pauseActivity = activity.getByRole("button", { name: "Pause activity", exact: true });
  await pauseActivity.waitFor();
  assert.ok(await activity.evaluate(element => element.contains(document.activeElement)));
  const controlInView = await pauseActivity.evaluate(element => {
    const rect = element.getBoundingClientRect();
    return rect.top >= 0 && rect.bottom <= window.innerHeight;
  });
  assert.ok(controlInView, "The visitor should see the activity playback control.");
  const seek = activity.getByRole("slider");
  const start = Number(await seek.inputValue());
  await page.clock.runFor(1000);
  assert.ok(Number(await seek.inputValue()) > start, "The header control should start playback on the first click.");
  await pauseActivity.click();

  // A direct link to the detailed transcript still starts its own replay.
  await page.goto("about:blank");
  await page.goto(url + "#play");
  const pause = page.locator("#v-research").getByRole("button", { name: "Pause replay", exact: true });
  await pause.waitFor();
  assert.equal(page.url(), url + "#play");
  await pause.click();
}

async function testExampleDeals(page, url) {
  await page.goto(url);
  const overview = page.locator("#v-findings");
  const expected = [
    ["Hemp-derived THC product sales", "Brad Asher"],
    ["Green Thumb brand licensing fees", "Sanjay Tolia"],
    ["U.S. WAKIX net product revenue", "Peter Anastasiou"],
    ["Event-contract revenue", "Shiv Verma"],
  ];
  assert.equal(await overview.getByRole("button", { name: /^Preview deal\b/ }).count(), expected.length);
  const panelIds = new Set();
  for (const [stream, contact] of expected) {
    const caseLink = overview.getByRole("link", { name: stream, exact: true });
    const row = overview.getByRole("row").filter({ has: page.getByRole("link", { name: stream, exact: true }) });
    const toggle = row.getByRole("button", { name: /^Preview deal\b/ });
    const id = await toggle.getAttribute("aria-controls");
    assert.ok(id && !panelIds.has(id), `Each prospect needs its own preview: ${stream}`);
    panelIds.add(id);
    const deal = overview.locator(`[id="${id}"]`);
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    assert.equal(await deal.isVisible(), false);
    await toggle.press("Enter");
    await deal.waitFor();
    assert.equal(await toggle.getAttribute("aria-expanded"), "true");
    assert.ok((await deal.innerText()).includes(stream));
    assert.ok((await deal.innerText()).includes(contact));
    const outreach = await deal.locator("blockquote").innerText();
    assert.ok(outreach.trim());
    const caseHref = await caseLink.getAttribute("href");
    assert.equal(await deal.getByRole("link", { name: /open case/i }).getAttribute("href"), caseHref);
    await toggle.press("Enter");
    assert.equal(await deal.isVisible(), false);

    await caseLink.click();
    const casePage = page.locator("#v-case");
    await casePage.getByRole("heading", { name: stream, exact: true }).waitFor();
    assert.equal(await casePage.getByRole("heading", { name: "Evidence", exact: true }).isVisible(), true);
    assert.ok(await casePage.locator('.ev a[href^="https://"]').count());
    assert.equal((await casePage.locator(".opening").textContent()).trim(), outreach.trim());
    await page.goBack();
    await overview.waitFor();
  }

  // Bookmarks to the original example still open the same company's sales case.
  await page.goto(url + "#hubspot");
  const legacyDeal = overview.locator("#hubspot");
  await legacyDeal.waitFor();
  assert.match(await legacyDeal.innerText(), /Hemp-derived THC product sales/);
  assert.match(await legacyDeal.innerText(), /Brad Asher/);
  assert.equal(await overview.locator('button[aria-controls="hubspot"]').getAttribute("aria-expanded"), "true");
}

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
  await nav.getByRole("link", { name: "Overview", exact: true }).click();
  const overview = page.locator("#v-findings");
  await overview.waitFor();
  const activity = overview.locator("#activity");
  assert.equal(await activity.isVisible(), true);
  assert.equal(await overview.locator("#also").isVisible(), true);
  const seek = activity.getByRole("slider");
  const toggle = activity.locator("#activity-toggle");
  assert.equal(await toggle.innerText(), "Replay activity · 0:59");
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
  await seek.press("End");
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

async function testActivityEntryLayout(page, url) {
  for (const width of [320, 1280]) {
    await page.setViewportSize({ width, height: 800 });
    await page.goto(url);
    const seek = page.locator("#activity-seek");
    const entry = page.locator("#activity-entry");
    const label = entry.locator("#activity-entry-label");
    const link = entry.getByRole("link");
    const heights = [];
    // The long SEC search at 26:53 sits between two shorter filing lookups.
    for (const [second, step] of [[1605, 99], [1621, 101], [1613, 100]]) {
      await seek.evaluate((slider, value) => {
        slider.value = value;
        slider.dispatchEvent(new Event("input", { bubbles: true }));
      }, second);
      assert.equal(await link.getAttribute("href"), "#step/" + step);
      const row = await entry.boundingBox(), action = await label.boundingBox(), log = await link.boundingBox();
      assert.ok(row && action && log && action.width > 0, "The action and log link should both be visible.");
      assert.ok(Math.abs(action.y + action.height / 2 - log.y - log.height / 2) <= 2,
        "The action and its log link should share a line.");
      assert.ok(action.x >= row.x - 1 && action.x + action.width <= log.x + 1 && log.x + log.width <= row.x + row.width + 1,
        "The action and log link should fit side by side without overlap.");
      assert.ok(row.x >= 0 && row.x + row.width <= width + 1);
      heights.push(row.height);
    }
    assert.ok(Math.max(...heights) - Math.min(...heights) <= 1, "Action length should not change the replay panel height.");
    assert.match(await label.textContent(), /Paragraph IV/);
    await link.press("Enter");
    await page.locator("#v-research #s100").waitFor();
    assert.equal(page.url(), url + "#step/100");
  }
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
    await testSearchBriefAndReplay(page, url);
    await testExampleDeals(page, url);
    await testReplay(page, url);
    await testActivity(page, url);
    await testActivityEntryLayout(page, url);
    console.log("Search brief, full replay, deal previews, case evidence, transcript continuity, saved-finding playback and research activity passed.");
  } finally {
    await browser.close();
  }
}

main().catch(error => { console.error(error.message); process.exitCode = 1; });
