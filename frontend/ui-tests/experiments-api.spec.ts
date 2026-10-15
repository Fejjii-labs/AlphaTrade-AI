import { expect, test } from "@playwright/test";
import fs from "node:fs/promises";
import path from "node:path";

test("Strategies lifecycle persists through real API reads, ambiguous responses and reload", async ({ page, request }) => {
  const base = process.env.PLAYWRIGHT_API_URL;
  const authFile = process.env.EXPERIMENT_BROWSER_AUTH_FILE;
  test.skip(!base || !authFile, "Requires an explicit synthetic local PostgreSQL fixture and API.");
  expect(["127.0.0.1", "localhost"]).toContain(new URL(base!).hostname);
  const auth = JSON.parse(await fs.readFile(authFile!, "utf8"));
  const headers = { Authorization: `Bearer ${auth.token}` };
  const frontend = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:3000";
  const images = process.env.EXPERIMENT_SCREENSHOTS_DIR ?? "../artifacts/experiments";
  await fs.mkdir(images, { recursive: true });
  const health = await (await request.get(`${base}/health`)).json();
  expect(health.execution_mode).toBe("paper"); expect(health.real_trading_enabled).toBe(false);
  const seedResponse = await request.get(`${base}/experiments/${auth.experiment_id}`, { headers });
  expect(seedResponse.ok()).toBe(true);
  const seed = await seedResponse.json();
  const configuration = seed.versions[0].configuration;
  const title = `Nested lifecycle ${crypto.randomUUID().slice(0, 4)}`;
  const createdResponse = await request.post(`${base}/experiments`, { headers, data: {
    name: title, idempotency_key: title, configuration,
  } });
  expect(createdResponse.status()).toBe(201);
  const created = await createdResponse.json();
  expect(created.runtime_activated).toBe(false); expect(created.performance).toBeNull();
  await page.context().addCookies([{ name: "alphatrade_session", value: "1", url: frontend }]);
  await page.addInitScript(token => { sessionStorage.setItem("alphatrade_access_token", token); }, auth.token);
  await page.goto("/strategies");
  const open = () => page.getByRole("button", { name: `Open ${title}`, exact: true });
  await expect(open()).toBeVisible();
  const cards = page.getByTestId("experiments");
  await expect(cards.getByText("Exploration", { exact: true }).first()).toBeVisible();
  await expect(cards.getByText("Validation", { exact: true }).first()).toBeVisible();
  expect(await cards.innerText()).not.toContain(created.experiment_id);
  expect(await cards.innerText()).not.toContain(created.configuration_hash);
  await page.screenshot({ path: path.join(images, "strategies-desktop.png"), fullPage: true });
  await open().click();
  await expect(page.getByRole("button", { name: "Request approval" })).toBeVisible();
  await page.getByRole("button", { name: "Request approval" }).click();
  await expect(page.getByText("Owner approval of the exact configuration is required.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Start experiment" })).toHaveCount(0);
  const submitted = await (await request.get(`${base}/experiments/${created.experiment_id}`, { headers })).json();
  const current = submitted.versions[0];
  const approval = await request.post(`${base}/experiments/${created.experiment_id}/versions/${current.id}/approve`, { headers, data: {
    expected_revision: current.revision, configuration_hash: current.configuration_hash,
    authorized_until: new Date(Date.now() + 3_600_000).toISOString(), confirm: "APPROVE_BOUNDED_EXPERIMENT",
  } });
  expect(approval.ok()).toBe(true);
  await page.getByRole("button", { name: "Refresh experiment", exact: true }).click();
  await expect(page.getByRole("button", { name: "Start experiment" })).toBeVisible();

  // Server commits the domain transition; the browser loses its reply. No retry is automatic.
  const transitionPath = `${base}/experiments/${created.experiment_id}/versions/${current.id}/transition`;
  let calls = 0;
  await page.route(transitionPath, async route => {
    calls++;
    expect(route.request().postDataJSON()).toEqual({ action: "start", expected_revision: current.revision + 1 });
    const committed = await route.fetch();
    expect(committed.ok()).toBe(true); expect((await committed.json()).runtime_activated).toBe(false);
    await route.abort("failed");
  });
  await page.getByRole("button", { name: "Start experiment" }).click();
  await expect(page.getByText("Change could not be confirmed. Refresh before retrying.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Start experiment" })).toBeDisabled();
  expect(calls).toBe(1);
  await page.unroute(transitionPath);
  await page.getByRole("button", { name: "Refresh experiment", exact: true }).click();
  const details = page.getByTestId("experiment-details");
  await expect(details.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
  expect(calls).toBe(1);
  await expect(page.getByText("Performance unavailable", { exact: true })).toBeVisible();
  await expect(page.getByText("Configuration & evidence").locator("..")).not.toHaveAttribute("open");
  await page.screenshot({ path: path.join(images, "experiment-details.png"), fullPage: true });
  await details.getByRole("button", { name: "Pause", exact: true }).click();
  await expect(page.getByRole("button", { name: "Resume experiment" })).toBeVisible();
  await page.reload();
  await open().click();
  await expect(page.getByRole("button", { name: "Resume experiment" })).toBeVisible();
  const persisted = await (await request.get(`${base}/experiments/${created.experiment_id}`, { headers })).json();
  expect(persisted.versions[0].state).toBe("paused"); expect(persisted.versions[0].runtime_activated).toBe(false);
  await page.getByRole("button", { name: /Back to experiments/ }).click();
  await expect(open()).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(open()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: path.join(images, "strategies-mobile.png"), fullPage: true });

  const sample = await request.post(`${base}/experiments/${created.experiment_id}/versions/${current.id}/samples`, { headers, data: { variant_key: "baseline", source_record_id: "synthetic-untrusted" } });
  expect(sample.status()).toBe(503);
  const otherResponse = await request.post(`${base}/auth/register`, { data: {
    email: `browser-other-${crypto.randomUUID()}@example.com`, password: "Synthetic-next-batch-42!", organization_name: "Synthetic separate tenant",
  } });
  expect(otherResponse.ok()).toBe(true);
  const other = await otherResponse.json();
  const inaccessible = await request.get(`${base}/experiments/${created.experiment_id}`, { headers: { Authorization: `Bearer ${other.tokens.access_token}` } });
  expect(inaccessible.status()).toBe(404);
});
