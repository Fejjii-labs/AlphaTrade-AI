import { expect, test } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

const stateFile = "/tmp/alphatrade-next-postgres/research-browser-state.json";
async function prerequisite() {
  const base = process.env.PLAYWRIGHT_API_URL!;
  const authFile = process.env.EXPERIMENT_BROWSER_AUTH_FILE!;
  expect(base && authFile).toBeTruthy();
  expect(["127.0.0.1", "localhost"]).toContain(new URL(base).hostname);
  return { base, auth: JSON.parse(await fs.readFile(authFile, "utf8")), frontend: process.env.PLAYWRIGHT_BASE_URL! };
}

test("author a document through Agent, persist a bounded draft and retain an ambiguous screening", async ({ page, request }) => {
  test.setTimeout(150_000);
  const { base, auth, frontend } = await prerequisite();
  const processIdentity = (await request.get(`${base}/health`)).headers()["x-synthetic-fixture-process"];
  expect(processIdentity).toBeTruthy();
  await page.context().addCookies([{ name: "alphatrade_session", value: "1", url: frontend }]);
  await page.addInitScript(token => sessionStorage.setItem("alphatrade_access_token", token), auth.token);
  let imports = 0;
  let agentRequests = 0;
  page.on("request", req => { if (req.method() === "POST" && req.url().endsWith("/knowledge/files/import")) imports++; if (req.method() === "POST" && req.url().endsWith("/agent/turns")) agentRequests++; });
  await page.goto("/agent");
  await expect(page.getByLabel("Message", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Attach document", exact: true }).click();
  await page.getByLabel("Attach document", { exact: true }).setInputFiles({ name: "synthetic-trendpulse.md", mimeType: "text/markdown", buffer: Buffer.from(`Synthetic authored research reference. This is not approval.\n\n\`\`\`json\n${JSON.stringify(auth.research_spec)}\n\`\`\``) });
  await page.getByRole("button", { name: "Preview attachment" }).click();
  await expect(page.getByLabel("Attachment preview")).toBeVisible();
  await page.getByLabel("Message", { exact: true }).fill("x".repeat(8001));
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByText(/Invalid API request/)).toBeVisible(); expect(agentRequests).toBe(0); expect(imports).toBe(1);
  await page.getByLabel("Message", { exact: true }).fill("Review this captured strategy for a bounded research draft.");
  await expect(page.getByRole("button", { name: "Send", exact: true })).toBeEnabled();
  await page.getByText("Strategy options", { exact: true }).click();
  await page.getByLabel("Strategy setup type").selectOption("manual_review");
  await page.getByRole("button", { name: "Review strategy draft", exact: true }).click();
  const save = page.getByRole("button", { name: "Confirm and save strategy version" });
  await expect(save).toBeEnabled();
  expect(imports).toBe(1);
  await save.click();
  const saved = page.getByRole("link", { name: "Open saved strategy" });
  await expect(saved).toBeVisible();
  const strategyPath = await saved.getAttribute("href");
  await saved.click();
  const draft = page.getByTestId("experiment-draft");
  await expect(draft.getByText("Prepare research draft")).toBeVisible();
  await draft.getByText("Prepare research draft").click();
  await draft.getByLabel("Experiment name").fill("Captured TrendPulse exploration");
  for (const [label, value] of [["Risk per trade", "10"], ["Position notional", "500"], ["Total exposure", "1000"], ["Daily loss", "50"], ["Weekly loss", "100"], ["Drawdown", "100"], ["Cost allowance", "1"]]) await draft.getByLabel(`${label} (USDT)`).fill(value);
  await draft.getByRole("button", { name: "Create experiment draft" }).click();
  const open = draft.getByRole("link", { name: "Open experiment" });
  await expect(open).toBeVisible();
  const target = await open.getAttribute("href");
  const experimentId = new URL(target!, frontend).searchParams.get("experiment");
  const headers = { Authorization: `Bearer ${auth.token}` };
  const persisted = await (await request.get(`${base}/experiments/${experimentId}`, { headers })).json();
  const version = persisted.versions[0];
  expect(version.state).toBe("draft"); expect(version.configuration.family).toBe("trendpulse_1r/v1");
  expect(version.configuration.model_policy.mode).toBe("disabled"); expect(version.configuration.account.source).toBe("internal_simulation");
  expect(version.runtime_activated).toBe(false); expect(version.performance).toBeNull();
  await open.click();
  await expect(page.getByTestId("experiment-details").getByRole("button", { name: "Request approval" })).toBeVisible();
  const screening = page.getByTestId("screenings");
  await expect(screening.getByText("No screenings recorded.")).toBeVisible();
  await screening.getByText("Screen a closed trigger").click();
  await screening.getByLabel("Trigger close (UTC)").fill(auth.trigger_end.slice(0, 16));
  const routeUrl = `${base}/experiments/${experimentId}/versions/${version.id}/trendpulse-screenings`;
  let calls = 0;
  let original: Record<string, unknown> = {};
  let receipt: Record<string, unknown> = {};
  await page.route(routeUrl, async route => {
    if (route.request().method() !== "POST") return route.continue();
    calls++; original = route.request().postDataJSON();
    const result = await route.fetch();
    expect(result.ok()).toBe(true); receipt = await result.json();
    expect(receipt.status).toBe("qualified_research_signal"); expect(receipt.receipt_provenance).toBe("synthetic_fixture");
    expect(receipt.performance).toBeNull(); expect(receipt.execution_authorized).toBe(false); expect(receipt.sample_eligible).toBe(false);
    // Actual server commit, successful but malformed response. Completion stays possible.
    await route.fulfill({ status: 200, contentType: "application/json", body: '{"unexpected":true}' });
  });
  await screening.getByRole("button", { name: "Screen trigger" }).click();
  await expect(screening.getByRole("button", { name: "Recover screening" })).toBeEnabled();
  expect(calls).toBe(1); expect(imports).toBe(1);
  const storage = await page.evaluate(() => Object.fromEntries(Object.entries(sessionStorage)));
  await fs.writeFile(stateFile, JSON.stringify({ token: auth.token, target, experimentId, versionId: version.id, strategyPath, original, receipt, storage, processIdentity }), { mode: 0o600 });
});

test("after an actual API restart, recover original screening and retain tenant-safe history", async ({ page, request }) => {
  const { base, frontend } = await prerequisite();
  const state = JSON.parse(await fs.readFile(stateFile, "utf8"));
  const processIdentity = (await request.get(`${base}/health`)).headers()["x-synthetic-fixture-process"];
  expect(processIdentity).toBeTruthy(); expect(processIdentity).not.toBe(state.processIdentity);
  const headers = { Authorization: `Bearer ${state.token}` };
  await page.context().addCookies([{ name: "alphatrade_session", value: "1", url: frontend }]);
  await page.addInitScript(storage => {
    // Restore once into the fresh tab; a reload must use the browser's real state,
    // not reintroduce the pending identity that successful recovery removed.
    if (!sessionStorage.getItem("alphatrade_access_token")) {
      for (const [key, value] of Object.entries(storage)) sessionStorage.setItem(key, String(value));
    }
  }, state.storage);
  await page.goto(state.target);
  const panel = page.getByTestId("screenings");
  await expect(panel.getByRole("button", { name: "Recover screening" })).toBeEnabled();
  const url = `${base}/experiments/${state.experimentId}/versions/${state.versionId}/trendpulse-screenings`;
  const before = await (await request.get(url, { headers })).json();
  expect(before.total).toBe(1); expect(before.items[0].id).toBe(state.receipt.id);
  let calls = 0;
  await page.route(url, async route => {
    if (route.request().method() === "POST") { calls++; expect(route.request().postDataJSON()).toEqual(state.original); }
    await route.continue();
  });
  expect(calls).toBe(0);
  await panel.getByRole("button", { name: "Recover screening" }).click();
  await expect(panel.getByText("Receipts & evidence")).toBeVisible(); expect(calls).toBe(1);
  const after = await (await request.get(url, { headers })).json(); expect(after.total).toBe(1);
  const exact = await (await request.get(`${base}/trendpulse-screenings/${state.receipt.id}`, { headers })).json(); expect(exact).toEqual(state.receipt);
  await page.unroute(url);
  const duplicate = await request.post(url, { headers, data: { ...state.original, request_id: crypto.randomUUID() } });
  expect(duplicate.ok()).toBe(true); const repeated = await duplicate.json(); expect(repeated.status).toBe("duplicate"); expect(repeated.duplicate_of).toBe(state.receipt.id);
  const rejection = await request.post(url, { headers, data: { ...state.original, request_id: crypto.randomUUID(), trigger_end: "2026-10-10T12:00:00Z" } });
  expect(rejection.ok()).toBe(true); expect((await rejection.json()).reason).toBe("trigger_expired");
  await panel.getByRole("button", { name: /Back to screening history/ }).click();
  await expect(panel.getByText("Rejected · trigger expired")).toBeVisible();
  expect(await panel.innerText()).not.toContain(state.receipt.id);
  const images = process.env.EXPERIMENT_SCREENSHOTS_DIR!; await fs.mkdir(images, { recursive: true });
  await page.screenshot({ path: path.join(images, "research-history.png"), fullPage: true });
  await panel.getByRole("button", { name: "Details", exact: true }).first().click();
  await expect(panel.getByText("Receipts & evidence")).toBeVisible();
  await expect(panel.getByText("Receipts & evidence").locator("..")).not.toHaveAttribute("open");
  await page.screenshot({ path: path.join(images, "research-details.png"), fullPage: true });
  await page.getByRole("button", { name: /Back to experiments/ }).click();
  await expect(page.getByRole("button", { name: "Open Captured TrendPulse exploration" })).toBeVisible();
  await page.reload(); await expect(page.getByTestId("screenings").getByText("Rejected · trigger expired")).toBeVisible();
  await expect(page.getByTestId("screenings").getByRole("button", { name: "Recover screening" })).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 }); expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(images, "research-mobile.png"), fullPage: true });
  const otherResponse = await request.post(`${base}/auth/register`, { data: { email: `research-other-${crypto.randomUUID()}@example.com`, password: "Synthetic-research-42!", organization_name: `Synthetic separate tenant ${crypto.randomUUID()}` } });
  expect(otherResponse.ok()).toBe(true); const other = await otherResponse.json(); const otherHeaders = { Authorization: `Bearer ${other.tokens.access_token}` };
  expect((await request.get(url, { headers: otherHeaders })).status()).toBe(404);
  expect((await request.get(`${base}/trendpulse-screenings/${state.receipt.id}`, { headers: otherHeaders })).status()).toBe(404);
  expect((await request.post(url, { headers: otherHeaders, data: state.original })).status()).toBe(404);
  expect((await request.post(`${base}/experiments/${state.experimentId}/versions/${state.versionId}/samples`, { headers, data: { variant_key: "baseline", source_record_id: state.receipt.id } })).status()).toBe(503);
});
