/** Browser coverage for five destinations, retained routes and durable source import. */
import path from "node:path";
import { expect, test, type Page } from "@playwright/test";
import {
  installSharedE2ESession,
  paperModeActive,
} from "./helpers/shared-e2e-auth";
const SHOTS =
  process.env.SIMPLIFIED_UI_SHOTS ?? "/opt/cursor/artifacts/screenshots";
const API_URL = process.env.PLAYWRIGHT_API_URL ?? "http://localhost:8000";
const DESTINATIONS = [
  { route: "/", heading: "Dashboard", file: "dashboard" },
  { route: "/agent", heading: "Agent", file: "agent" },
  { route: "/journal", heading: "Journal & Knowledge", file: "journal" },
  { route: "/strategies", heading: "Strategies", file: "strategies" },
  { route: "/settings", heading: "Settings", file: "settings" },
] as const;
const RETAINED = [
  "/knowledge",
  "/strategy-lab",
  "/lessons",
  "/settings/advanced",
];
async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
}
test("desktop/mobile five destinations and preview→Send retains a source without execution", async ({
  page,
  request,
}) => {
  test.setTimeout(180_000);
  await page.addInitScript(() =>
    Object.defineProperty(window, "SpeechRecognition", {
      configurable: true,
      value: class {},
    }),
  );
  const accessToken = await installSharedE2ESession(page, request);
  for (const viewport of [
    { width: 1280, height: 900, label: "desktop" },
    { width: 390, height: 844, label: "mobile" },
  ]) {
    await page.setViewportSize(viewport);
    for (const destination of DESTINATIONS) {
      await page.goto(destination.route);
      await expect(
        page.getByRole("heading", {
          level: 1,
          name: destination.heading,
          exact: true,
        }),
      ).toBeVisible();
      await expect(paperModeActive(page)).toBeVisible();
      await expect(
        page.getByRole("button", { name: /place real order|execute live/i }),
      ).toHaveCount(0);
      await noOverflow(page);
      const nav =
        viewport.width >= 1024
          ? page.getByRole("navigation", { name: "Primary destinations" })
          : page.getByTestId("mobile-bottom-navigation");
      await expect(nav.getByRole("link")).toHaveCount(5);
      await page.screenshot({
        path: path.join(SHOTS, `${viewport.label}-${destination.file}.png`),
        fullPage: true,
        animations: "disabled",
      });
    }
    const nav =
      viewport.width >= 1024
        ? page.getByRole("navigation", { name: "Primary destinations" })
        : page.getByTestId("mobile-bottom-navigation");
    await page.evaluate(() => {
      const portal = document.querySelector("nextjs-portal");
      if (portal instanceof HTMLElement) portal.style.pointerEvents = "none";
    });
    for (const destination of DESTINATIONS) {
      await nav
        .getByRole("link", { name: destination.heading, exact: true })
        .click();
      await expect(
        page.getByRole("heading", {
          level: 1,
          name: destination.heading,
          exact: true,
        }),
      ).toBeVisible();
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/agent");
  await expect(page.getByTestId("agent-workspace")).toBeVisible();
  const voice = page.getByRole("region", { name: "Voice conversation" });
  await expect(voice).toBeVisible();
  await expect(voice.getByRole("button", { name: "Record", exact: true })).toBeEnabled();
  await expect(page.getByTestId("agent-attach-image")).toHaveCount(0);
  const title = `Agent import smoke ${Date.now()}`;
  const text =
    "Reference guidance: wait for a confirmed setup. This is proposed guidance.";
  const imports: string[] = [];
  page.on("request", (r) => {
    if (
      r.method() === "POST" &&
      new URL(r.url()).pathname === "/knowledge/files/import"
    )
      imports.push(r.url());
  });
  await page
    .getByRole("button", { name: "Attach document", exact: true })
    .click();
  await page
    .locator('input[type="file"]')
    .setInputFiles({
      name: `${title}.txt`,
      mimeType: "text/plain",
      buffer: Buffer.from(text),
    });
  const preview = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/knowledge/files/preview" &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Preview attachment", exact: true })
    .click();
  expect((await preview).ok()).toBe(true);
  await expect(
    page.getByLabel("Attachment preview", { exact: true }),
  ).toHaveValue(text);
  expect(imports).toHaveLength(0);
  const listDocuments = async () => {
    const response = await request.get(
      `${API_URL}/knowledge/documents?limit=100`,
      { headers: { Authorization: `Bearer ${accessToken}` } },
    );
    expect(response.ok()).toBe(true);
    return (await response.json()) as {
      items: { id: string; title: string }[];
    };
  };
  expect((await listDocuments()).items.some((d) => d.title === title)).toBe(
    false,
  );
  const imported = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/knowledge/files/import" &&
      r.request().method() === "POST",
  );
  const turn = page.waitForResponse(
    (r) =>
      new URL(r.url()).pathname === "/agent/turns" &&
      r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Send", exact: true }).click();
  const source = await imported;
  expect(source.ok()).toBe(true);
  const stored = await source.json();
  expect(stored.sql_chunks_stored).toBe(true);
  const response = await turn;
  expect(response.ok()).toBe(true);
  expect(await response.json()).toMatchObject({
    authority_mutated: false,
    execution_attempted: false,
    capture_status: "failed",
    saved_entries: [],
  });
  // Mock reasoning is explicitly unavailable; source persistence is real.
  await expect(page.getByTestId("saved-receipt")).toContainText(
    "retained in this conversation",
  );
  expect(imports).toHaveLength(1);
  expect((await listDocuments()).items).toContainEqual(
    expect.objectContaining({ id: stored.document_id, title }),
  );
  await page.reload();
  await expect(page.getByTestId("agent-workspace")).toBeVisible();
  await expect(page.getByTestId("saved-receipt")).toContainText("retained in this conversation");
  await expect(page.getByRole("link", { name: "Open original document" })).toHaveAttribute(
    "href", `/journal?tab=knowledge&document_id=${stored.document_id}`,
  );
  for (const route of RETAINED) {
    await page.goto(route);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expect(paperModeActive(page)).toBeVisible();
    await noOverflow(page);
  }
});
