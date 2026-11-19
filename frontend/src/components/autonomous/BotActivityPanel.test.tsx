import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import type { BotAction } from "@/lib/api/types";
import { expectNoIdentifiers } from "@/test/no-identifiers";

import { actionSentence, BotActivityPanel } from "./BotActivityPanel";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

const action = (over: Partial<BotAction>): BotAction => ({ at: "2026-11-04T03:47:00Z", outcome: "blocked", ...over });

describe("actionSentence", () => {
  it("says what happened in one plain sentence, never a reason code", () => {
    expect(actionSentence(action({ outcome: "entered", symbol: "BTCUSDT", direction: "long" }))).toBe("Opened BTCUSDT long");
    expect(actionSentence(action({ symbol: "ETHUSDT", direction: "SHORT", reason: "insufficient_daily_loss_allocation" })))
      .toBe("Skipped ETHUSDT short: Daily loss budget used up.");
    expect(actionSentence(action({ outcome: "rejected", reason: "weird_internal_code" }))).toBe("Rejected: Skipped by a safety check.");
    expect(actionSentence(action({}))).toBe("Skipped");
  });
});

describe("BotActivityPanel", () => {
  it("lists the last five decisions with a trade link when there is one", async () => {
    vi.spyOn(api.autonomous, "activity").mockResolvedValue({
      day: "2026-11-04", timezone: "UTC", trades_today: 0,
      recent_actions: [
        action({ outcome: "entered", symbol: "BTCUSDT", direction: "long", journal_trade_id: "11111111-1111-4111-8111-111111111111" }),
        ...Array.from({ length: 6 }, () => action({ reason: "demo_leverage_exceeds_profile_max" })),
      ],
    });
    render(<BotActivityPanel />);
    const list = await screen.findByTestId("bot-activity");
    expect(list.querySelectorAll("li")).toHaveLength(5);
    expect(list).toHaveTextContent("Opened BTCUSDT long");
    expect(list).toHaveTextContent("Skipped: Leverage is above the risk profile maximum.");
    expect(screen.getByRole("link", { name: "Trade" })).toHaveAttribute("href", "/journal?trade_id=11111111-1111-4111-8111-111111111111");
    expect(list).not.toHaveTextContent("demo_leverage");
    expectNoIdentifiers();
  });

  it("says when there is nothing yet or the read failed", async () => {
    vi.spyOn(api.autonomous, "activity").mockResolvedValue({ day: "d", timezone: "UTC", trades_today: 0, recent_actions: [] });
    render(<BotActivityPanel />);
    expect(await screen.findByText("No decisions yet.")).toBeInTheDocument();
    cleanup();
    vi.mocked(api.autonomous.activity).mockRejectedValue(new Error("down"));
    render(<BotActivityPanel />);
    expect(await screen.findByText("Bot activity unavailable.")).toBeInTheDocument();
  });
});
