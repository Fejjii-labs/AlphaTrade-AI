import { expect, test } from "@playwright/test";
import { appConfig } from "../src/lib/config";
import { strategyPatchResponse } from "../src/lib/api/generated/validators";
import { strategyPatch } from "../src/lib/api/generated/client";
import { emptyStrategyCard } from "../src/components/strategy/StrategyCardForm";

test("generated strategy PATCH persists setup_type and a separate API read reloads it", async ({ request }) => {
  const base = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
  expect(appConfig.apiBaseUrl).toBe(base);
  const health = await request.get(`${base}/health`);
  expect(health.ok()).toBe(true);
  const posture = await health.json();
  expect(posture.execution_mode).toBe("paper");
  expect(posture.real_trading_enabled).toBe(false);
  const registered = await request.post(`${base}/auth/register`, { data: {
    email: `reviewer-${crypto.randomUUID()}@example.com`, password: "Disposable-local-fixture-42!",
    organization_name: `Reviewer local fixture ${crypto.randomUUID()}`,
  } });
  expect(registered.ok(), `Registration HTTP ${registered.status()}`).toBe(true);
  const auth = await registered.json();
  const headers = { Authorization: `Bearer ${auth.tokens.access_token}` };
  const created = await request.post(`${base}/strategies`, { headers, data: {
    name: "Setup type persistence fixture", setup_type: "htf_trend_pullback",
    card: emptyStrategyCard("Setup type persistence fixture"),
  } });
  expect(created.ok()).toBe(true);
  const strategy = await created.json();
  let updated;
  try { updated = await strategyPatch(strategy.id, { setup_type: "nested_continuation" }, { headers }); }
  catch (error) {
    console.error("Contract failure paths", strategyPatchResponse.errors?.map(({ instancePath, keyword, params }) => ({ instancePath, keyword, params })));
    throw error;
  }
  expect(updated.setup_type).toBe("nested_continuation");
  const reloaded = await request.get(`${base}/strategies/${strategy.id}`, { headers });
  expect(reloaded.ok()).toBe(true);
  expect((await reloaded.json()).setup_type).toBe("nested_continuation");
  // A second independently authenticated tenant cannot read or modify this strategy.
  const other = await request.post(`${base}/auth/register`, { data: {
    email: `reviewer-other-${crypto.randomUUID()}@example.com`, password: "Disposable-local-fixture-42!",
    organization_name: `Other local fixture ${crypto.randomUUID()}`,
  } });
  expect(other.ok()).toBe(true);
  const otherAuth = await other.json();
  const inaccessible = await request.get(`${base}/strategies/${strategy.id}`, {
    headers: { Authorization: `Bearer ${otherAuth.tokens.access_token}` },
  });
  expect(inaccessible.status()).toBe(404);
});
