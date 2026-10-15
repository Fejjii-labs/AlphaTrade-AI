import { test, expect, type Page } from "@playwright/test";

async function start(page: Page) {
  await page.getByRole("checkbox", { name: "Conversation mode" }).check();
  await page.getByRole("button", { name: "Start conversation" }).click();
}

for (const [name, width, height] of [["narrow", 320, 720], ["phone", 390, 844], ["desktop", 1440, 1000]] as const) {
  test(`${name}: acknowledged playback and explicit interruption fit without clipping`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto("/");
    expect(await page.evaluate(() => window.voiceHarness.listens)).toBe(0);
    await start(page);
    await expect(page.getByRole("status")).toContainText("Listening");
    const controls = page.getByRole("region", { name: "Voice conversation" });
    await controls.screenshot({ path: `../docs/screenshots/voice-conversation/${name}-listening.png` });
    await page.evaluate(() => {
      window.voiceHarness.partial("Partial"); window.voiceHarness.partial("Partial");
    });
    expect(await page.evaluate(() => window.voiceHarness.submissions.length)).toBe(0);
    await page.evaluate(() => window.voiceHarness.finish("Review risk"));
    await expect(page.getByRole("button", { name: "Stop speaking" })).toBeEnabled();
    expect(await page.evaluate(() => window.voiceHarness.listens)).toBe(1);
    expect(await page.evaluate(() => window.voiceHarness.submissions.length)).toBe(1);
    await controls.screenshot({ path: `../docs/screenshots/voice-conversation/${name}-speaking.png` });
    for (const button of await controls.getByRole("button").all()) {
      const box = await button.boundingBox();
      expect(box!.height).toBeGreaterThanOrEqual(44);
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole("button", { name: "Stop speaking" }).click();
    await page.evaluate(() => window.voiceHarness.playbackEnd());
    expect(await page.evaluate(() => window.voiceHarness.listens)).toBe(1);
    await expect(page.getByRole("status")).toContainText("Paused");
    await page.getByRole("button", { name: "Resume voice" }).click();
    await page.evaluate(() => window.voiceHarness.finish("Next turn"));
    await expect(page.getByRole("button", { name: "Stop speaking" })).toBeEnabled();
    await page.evaluate(() => window.voiceHarness.playbackEnd());
    await expect(page.getByRole("status")).toContainText("Listening");
    expect(await page.evaluate(() => window.voiceHarness.listens)).toBe(3);
    await page.getByRole("button", { name: "End conversation" }).click();
  });
}

test("typed drafts stay editable; uncertain outcome recovers the original turn", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 720 });
  await page.goto("/");
  await page.getByRole("textbox", { name: "Typed draft" }).fill("Keep my draft");
  await start(page);
  await page.evaluate(() => window.voiceHarness.finish("Voice draft"));
  await expect(page.getByRole("textbox", { name: "Typed draft" })).toHaveValue("Keep my draft");
  expect(await page.evaluate(() => window.voiceHarness.submissions.length)).toBe(0);
  await page.getByRole("textbox", { name: "Voice transcript" }).fill("Edited speech");
  await page.getByRole("button", { name: "Append to draft" }).click();
  await expect(page.getByRole("textbox", { name: "Typed draft" })).toHaveValue("Keep my draft\nEdited speech");
  await page.getByRole("textbox", { name: "Typed draft" }).fill("");
  await page.evaluate(() => { window.voiceHarness.result = "uncertain"; });
  await page.getByRole("button", { name: "Start conversation" }).click();
  await page.evaluate(() => window.voiceHarness.finish("Keep original identity"));
  await expect(page.getByRole("alert")).toContainText("Recover the original request");
  await page.getByRole("button", { name: "Recover original request" }).click();
  expect(await page.evaluate(() => JSON.stringify(window.voiceHarness.submissions[0]) === JSON.stringify(window.voiceHarness.recoveries[0]))).toBe(true);
  expect(await page.evaluate(() => window.voiceHarness.listens)).toBe(2);
});

test("permission failure keeps a usable typed fallback and puts details behind disclosure", async ({ page }) => {
  await page.goto("/"); await start(page);
  await page.evaluate(() => window.voiceHarness.deny());
  await expect(page.getByRole("alert")).toContainText("Retry or type your message");
  await page.getByRole("textbox", { name: "Typed draft" }).fill("Use typed fallback");
  await expect(page.getByRole("textbox", { name: "Typed draft" })).toHaveValue("Use typed fallback");
  await expect(page.getByText(/The browser rejected speech recognition access/)).toBeHidden();
});
