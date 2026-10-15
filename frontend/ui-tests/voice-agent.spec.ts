import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { agentTurnFixture, FIXTURE_UUID } from "../src/test/pilot-fixtures";

const otherConversation = "77777777-7777-4777-8777-777777777777";
const documentId = "44444444-4444-4444-8444-444444444444";
type SpeechHarness = { finish(text: string): void; partial(text: string): void; endPlayback(): void;
  deny(): void; listens: number; aborts: number; cancels: number; spoken: string[] };
declare global { interface Window { __voiceTest: SpeechHarness } }

/** Deterministic Web Speech events through the real browser provider; no microphone hardware. */
async function installSpeech(page: Page, supported = true) {
  await page.addInitScript(enabled => {
    const recognitions: Recognition[] = [];
    const current = () => recognitions.at(-1)!;
    let playback: Utterance;
    const harness: SpeechHarness = {
      finish: text => { current().onresult?.({ results: [{ isFinal: true, 0: { transcript: text } }] }); current().onend?.(); },
      partial: text => current().onresult?.({ results: [{ isFinal: false, 0: { transcript: text } }] }),
      endPlayback: () => playback.onend?.(), deny: () => current().onerror?.({ error: "not-allowed" }),
      listens: 0, aborts: 0, cancels: 0, spoken: [],
    };
    class Recognition {
      onstart: (() => void) | null = null;
      onend: (() => void) | null = null;
      onerror: ((event: { error: string }) => void) | null = null;
      onresult: ((event: { results: { isFinal: boolean; 0: { transcript: string } }[] }) => void) | null = null;
      start() { recognitions.push(this); harness.listens++; this.onstart?.(); }
      stop() {}
      abort() { harness.aborts++; }
    }
    class Utterance {
      onstart: (() => void) | null = null;
      onend: (() => void) | null = null;
      constructor(public text: string) {}
    }
    Object.defineProperty(window, "SpeechRecognition", { configurable: true, value: enabled ? Recognition : undefined });
    Object.defineProperty(window, "webkitSpeechRecognition", { configurable: true, value: undefined });
    Object.defineProperty(window, "SpeechSynthesisUtterance", { configurable: true, value: enabled ? Utterance : undefined });
    Object.defineProperty(window, "speechSynthesis", { configurable: true, value: enabled ? {
      speak: (utterance: Utterance) => { playback = utterance; harness.spoken.push(utterance.text); utterance.onstart?.(); },
      cancel: () => { harness.cancels++; },
    } : undefined });
    window.__voiceTest = harness;
  }, supported);
}

async function installApi(page: Page, uncertainFirst = false) {
  const turns: { body: Record<string, unknown>; key: string }[] = [];
  const counts = { creations: 0, imports: 0, forbidden: 0, history: 0 };
  await page.context().addCookies([{ name: "alphatrade_session", value: "1", url: "http://127.0.0.1:3000" }]);
  await page.addInitScript(() => sessionStorage.setItem("alphatrade_access_token", "voice-fixture-only"));
  await page.route("http://localhost:8000/**", async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (request.method() === "POST") {
      if (pathname === "/conversations") {
        counts.creations++;
        await route.fulfill({ json: { id: FIXTURE_UUID, title: "Voice review", strategy_id: null } });
      } else if (pathname === "/agent/turns") {
        const body = request.postDataJSON();
        const key = request.headers()["idempotency-key"];
        turns.push({ body, key });
        if (uncertainFirst && turns.length === 1) {
          await route.fulfill({ status: 503, json: { detail: "Fixture response uncertain" } });
        } else {
          const n = turns.length;
          await route.fulfill({ json: { ...agentTurnFixture, conversation_id: body.conversation_id ?? FIXTURE_UUID,
            user_message_id: `22222222-2222-4222-8222-${String(n).padStart(12, "0")}`,
            assistant_message_id: `33333333-3333-4333-8333-${String(n).padStart(12, "0")}`,
            reply: `Acknowledged spoken reply ${n}.` } });
        }
      } else if (pathname === "/knowledge/files/preview") {
        await route.fulfill({ json: { title: "rules", extracted_text: "Rules", warnings: [], preview_receipt: "fixture-receipt" } });
      } else if (pathname === "/knowledge/files/import") {
        counts.imports++;
        await route.fulfill({ json: { document_id: documentId, source_hash: "a".repeat(64), chunk_count: 1, version: 1 } });
      } else {
        counts.forbidden++;
        await route.fulfill({ status: 400, json: { detail: "Unexpected mutation in voice fixture" } });
      }
      return;
    }
    const pageOf = (items: unknown[]) => ({ items, total: items.length, limit: 100, offset: 0 });
    const fixtures: Record<string, unknown> = {
      "/health": { status: "ok", execution_mode: "paper", real_trading_enabled: false, provider_mode: "mock", must_verify_email: false },
      "/auth/me": { user: { id: FIXTURE_UUID, email: "voice-fixture@example.com", email_verified: true }, organization: { id: FIXTURE_UUID, name: "Voice fixture" } },
      "/providers/status": { providers: [] }, "/positions": pageOf([]), "/strategies": pageOf([]),
      "/risk/kill-switch": { active: false, global_active: false, execution_blocked: false },
      "/conversations": pageOf([{ id: FIXTURE_UUID, title: "Voice review" }, { id: otherConversation, title: "Another conversation" }]),
      [`/conversations/${FIXTURE_UUID}`]: { id: FIXTURE_UUID, title: "Voice review", strategy_id: null },
      [`/conversations/${otherConversation}`]: { id: otherConversation, title: "Another conversation", strategy_id: null },
    };
    if (pathname.endsWith("/messages")) {
      counts.history++;
      // Deliberately stale history must not erase the immediate acknowledgment.
      await route.fulfill({ json: pageOf([]) });
    } else await route.fulfill({ status: pathname in fixtures ? 200 : 503, json: fixtures[pathname] ?? { detail: "Fixture unavailable" } });
  });
  return { turns, counts };
}

async function begin(page: Page) {
  await page.getByRole("checkbox", { name: "Conversation mode" }).check();
  await page.getByRole("button", { name: "Start conversation", exact: true }).click();
  await expect(page.getByRole("region", { name: "Voice conversation" }).getByRole("status")).toContainText("Listening");
}
async function navigate(page: Page, name: string) {
  await page.getByRole("button", { name: "History", exact: true }).click();
  await page.getByRole("button", { name, exact: true }).click();
}

for (const [name, width, height] of [["narrow", 320, 720], ["phone", 390, 844], ["desktop", 1440, 1000]] as const) {
  test(`${name}: actual Agent creates a conversation and runs two successive spoken turns`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await installSpeech(page);
    const { turns, counts } = await installApi(page);
    await page.goto("/agent");
    expect(await page.evaluate(() => window.__voiceTest.listens)).toBe(0);
    await begin(page);
    await page.evaluate(() => { window.__voiceTest.partial("Partial"); window.__voiceTest.partial("Partial"); });
    expect(turns).toHaveLength(0);
    await page.evaluate(() => window.__voiceTest.finish("First spoken turn"));
    await expect(page.getByTestId("agent-thread")).toContainText("Acknowledged spoken reply 1.");
    await expect(page.getByRole("button", { name: "Stop speaking" })).toBeEnabled();
    const controls = page.getByRole("region", { name: "Voice conversation" });
    await controls.evaluate(element => element.scrollIntoView({ block: "center" }));
    await controls.screenshot({ path: path.resolve(`../docs/screenshots/voice-agent/${name}-integrated-speaking.png`) });
    expect(await page.evaluate(() => window.__voiceTest.listens)).toBe(1);
    await page.evaluate(() => window.__voiceTest.endPlayback());
    await expect(controls.getByRole("status")).toContainText("Listening");
    await page.evaluate(() => window.__voiceTest.finish("Second spoken turn"));
    await expect(page.getByTestId("agent-thread")).toContainText("Acknowledged spoken reply 2.");
    expect(turns).toHaveLength(2);
    expect(turns.map(t => t.body)).toEqual([
      { conversation_id: FIXTURE_UUID, message: "First spoken turn" },
      { conversation_id: FIXTURE_UUID, message: "Second spoken turn" },
    ]);
    expect(turns[0].key).toMatch(/^[0-9a-f-]{36}$/i);
    expect(turns[1].key).not.toBe(turns[0].key);
    expect(counts.creations).toBe(1);
    expect(counts.forbidden).toBe(0);
    const outcomes = await page.evaluate(scope => JSON.parse(sessionStorage.getItem(`alphatrade:turn-outcome:${scope}`)!), `${FIXTURE_UUID}:${FIXTURE_UUID}`);
    expect(outcomes).toHaveLength(2);
    expect(outcomes.every((proof: { origin: string }) => proof.origin === "voice")).toBe(true);
    await expect(controls).toHaveCount(1);
    for (const button of await controls.getByRole("button").all()) {
      const box = await button.boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    await page.getByRole("button", { name: "End conversation" }).click();
    await navigate(page, "Another conversation");
    await navigate(page, "Voice review");
    await expect(page.getByRole("button", { name: /Resume voice|Start conversation/ })).toBeEnabled();
    await expect(page.getByRole("button", { name: "Recover original request" })).toHaveCount(0);
    expect(turns).toHaveLength(2);
  });
}

test("clear and replace review text keeps typed drafts through send and conversation changes", async ({ page }) => {
  await installSpeech(page);
  const { turns, counts } = await installApi(page);
  await page.goto("/agent");
  const draft = page.getByRole("textbox", { name: "Message", exact: true });
  await draft.fill("Keep my typed draft");
  await begin(page);
  await page.evaluate(() => window.__voiceTest.finish("Original speech"));
  const transcript = page.getByRole("textbox", { name: "Voice transcript" });
  await transcript.fill("");
  await expect(transcript).toBeVisible();
  await expect(page.getByRole("button", { name: "Send transcript" })).toBeDisabled();
  await transcript.fill("Replacement speech");
  await page.getByRole("button", { name: "Send transcript" }).click();
  await expect(page.getByTestId("agent-thread")).toContainText("Acknowledged spoken reply 1.");
  await expect(draft).toHaveValue("Keep my typed draft");
  await navigate(page, "Another conversation");
  await draft.fill("Other draft");
  await navigate(page, "Voice review");
  await expect(draft).toHaveValue("Keep my typed draft");
  expect(turns[0].body.message).toBe("Replacement speech");
  expect(turns).toHaveLength(1);
  expect(counts.forbidden).toBe(0);
});

test("uncertain speech reloads with the exact attachment/body/key and recovers once", async ({ page }) => {
  await installSpeech(page);
  const { turns, counts } = await installApi(page, true);
  await page.goto(`/agent?conversation=${FIXTURE_UUID}`);
  await page.getByRole("button", { name: "Attach document", exact: true }).click();
  await page.getByLabel("Attach document", { exact: true }).setInputFiles({ name: "rules.txt", mimeType: "text/plain", buffer: Buffer.from("Rules") });
  await page.getByRole("button", { name: "Preview attachment" }).click();
  await expect(page.getByLabel("Attachment preview")).toBeVisible();
  await begin(page);
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Draft retained through recovery reload");
  await page.evaluate(() => window.__voiceTest.finish("Review original rules"));
  await page.getByRole("button", { name: "Send transcript" }).click();
  await expect(page.getByRole("button", { name: "Recover original request" })).toBeEnabled();
  const saved = await page.evaluate(scope => JSON.parse(sessionStorage.getItem(`alphatrade:pending-turn:${scope}`)!), `${FIXTURE_UUID}:${FIXTURE_UUID}`);
  expect(saved[0].voice).toMatchObject({ turnKey: turns[0].key, transcript: "Review original rules", origin: "voice" });
  await navigate(page, "Another conversation");
  await page.reload();
  await expect(page).toHaveURL(new RegExp(otherConversation));
  await navigate(page, "Voice review");
  const draft = page.getByRole("textbox", { name: "Message", exact: true });
  await expect(draft).toHaveValue("Draft retained through recovery reload");
  await draft.fill("Keep recovery draft");
  await page.getByRole("button", { name: "Recover original request" }).click();
  await expect(page.getByTestId("agent-thread")).toContainText("Acknowledged spoken reply 2.");
  expect(turns).toHaveLength(2);
  expect(turns[1]).toEqual(turns[0]);
  expect(turns[0].body.source_document_id).toBe(documentId);
  expect(counts.imports).toBe(1);
  expect(counts.forbidden).toBe(0);
  await expect(draft).toHaveValue("Keep recovery draft");
  await expect(page.getByRole("button", { name: "Recover original request" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Record|Resume voice/ })).toBeEnabled();
});

for (const cancel of ["navigation", "hidden"] as const) {
  test(`${cancel} stops capture and ignores late speech events`, async ({ page }) => {
    await installSpeech(page);
    const { turns } = await installApi(page);
    await page.goto("/agent"); await begin(page);
    await page.evaluate(() => window.__voiceTest.partial("Unsent speech"));
    const aborts = await page.evaluate(() => window.__voiceTest.aborts);
    if (cancel === "navigation") await navigate(page, "Another conversation");
    else await page.evaluate(() => {
      Object.defineProperty(document, "hidden", { configurable: true, value: true });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(await page.evaluate(() => window.__voiceTest.aborts)).toBeGreaterThan(aborts);
    await page.evaluate(() => window.__voiceTest.finish("Late speech"));
    expect(turns).toHaveLength(0);
    await expect(page.getByRole("button", { name: "Stop recording" })).toHaveCount(0);
  });
}

test("unsupported browser speech leaves typed Agent submission usable", async ({ page }) => {
  await installSpeech(page, false);
  const { turns } = await installApi(page);
  await page.goto("/agent");
  await expect(page.getByRole("button", { name: "Record" })).toBeDisabled();
  await expect(page.getByText("Voice input unavailable. Type your message.")).toBeVisible();
  await page.getByRole("textbox", { name: "Message", exact: true }).fill("Typed fallback");
  await page.getByRole("button", { name: "Send", exact: true }).click();
  await expect(page.getByTestId("agent-thread")).toContainText("Acknowledged spoken reply 1.");
  expect(turns[0].body).toEqual({ message: "Typed fallback" });
});
