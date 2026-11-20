import { expect, test, type Route } from "@playwright/test";

import { installSharedE2ESession } from "./helpers/shared-e2e-auth";

async function fulfillJson(route: Route, body: unknown, status = 200) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function isBackendApi(url: URL, pathPrefix: string): boolean {
  return url.port === "8000" && url.pathname.startsWith(pathPrefix);
}

test.describe("Deep-link contracts (AT-041 PR4)", () => {
  test.describe.configure({ mode: "serial" });

  test("invalid journal ?entry= shows limited-window notice and opens no unrelated entry", async ({
    page,
    request,
  }) => {
    await installSharedE2ESession(page, request);

    await page.route((url) => isBackendApi(url, "/journal/entries"), async (route) => {
      if (route.request().method() !== "GET") {
        await route.continue();
        return;
      }
      await fulfillJson(route, {
        items: [
          {
            id: "entry-present",
            organization_id: "org",
            user_id: "user",
            symbol: "BTCUSDT",
            timeframe: "1h",
            direction: "long",
            entry_rationale: "Present entry",
            emotions: [],
            mistakes: [],
            result: "win",
            tags: [],
            screenshot_refs: [],
            created_at: "2026-07-21T12:00:00.000Z",
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
      });
    });
    await page.route((url) => isBackendApi(url, "/positions"), async (route) => {
      await fulfillJson(route, { items: [], total: 0, limit: 50, offset: 0 });
    });

    await page.goto("/journal?entry=missing-entry-id");
    const journalNotice = page.getByTestId("journal-stale-entry");
    await expect(journalNotice).toBeVisible();
    await expect(journalNotice).toContainText(/most recent 50 journal entries/i);
    await expect(journalNotice).toContainText(/No unrelated entry was opened/i);
  });

  test("invalid knowledge ?document= shows exact-source unavailable notice", async ({ page, request }) => {
    await installSharedE2ESession(page, request);

    await page.route((url) => isBackendApi(url, "/knowledge"), async (route) => {
      await fulfillJson(route, {
        items: [],
        documents: [],
        chunks: [],
        total: 0,
        limit: 50,
        offset: 0,
      });
    });

    await page.goto("/knowledge?document=missing-document-id");
    const knowledgeNotice = page.getByTestId("knowledge-document-stale");
    await expect(knowledgeNotice).toBeVisible();
    await expect(knowledgeNotice).toContainText(/unavailable/i);
    await expect(knowledgeNotice).toContainText("No unrelated document was opened");
    await page.getByLabel("Knowledge category").selectOption("rules");
    await expect(page).toHaveURL(/\/knowledge\?category=rules$/);
    await expect(knowledgeNotice).toHaveCount(0);
  });
});
