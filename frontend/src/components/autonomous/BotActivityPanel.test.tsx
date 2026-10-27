import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AutonomousActivityView, AutonomousTradingSettingsView } from "@/lib/api/types";
import { expectNoIdentifiers } from "@/test/no-identifiers";
import { BotActivityPanel, botState } from "./BotActivityPanel";

const useAsyncDataMock = vi.fn();
vi.mock("@/hooks/useAsyncData", () => ({ useAsyncData: (...args: unknown[]) => useAsyncDataMock(...args) }));
vi.mock("@/lib/api", () => ({ api: { autonomous: { activity: vi.fn() } } }));

const TRADE = "11111111-2222-4333-8444-555555555555";
const view: AutonomousTradingSettingsView = {
  organization_id: "org",
  user_id: "user",
  daily_loss_limit_percent: "3",
  max_trades_per_day: 5,
  risk_per_trade_percent: null,
  strategy_id: "s-1",
  symbol: "BTCUSDT",
  using_defaults: false,
  account_max_trades_per_day: 3,
  effective_max_trades_per_day: 3,
  strategy_options: [{ strategy_id: "s-1", name: "Nested BTC" }],
  symbol_options: ["BTCUSDT"],
  max_daily_loss_percent_ceiling: "6",
  max_risk_per_trade_percent_ceiling: "5",
  internal_paper_enabled: true,
  internal_paper_effective: true,
  worker_runtime_health: "RUNNING",
  worker_flags_stale: false,
};
const activity = (over: Partial<AutonomousActivityView> = {}): AutonomousActivityView => ({
  day: "2026-10-27",
  timezone: "UTC",
  trades_today: 1,
  recent_actions: [],
  ...over,
});
const loaded = (data: AutonomousActivityView | null, extra: Record<string, unknown> = {}) =>
  useAsyncDataMock.mockReturnValue({ data, loading: false, error: null, reload: vi.fn(), ...extra });
const tiles = () =>
  Object.fromEntries(
    screen.getAllByTestId("status-tile").map((tile) => [
      tile.querySelector("span")!.textContent,
      [tile.textContent!.replace(tile.querySelector("span")!.textContent!, ""), tile.dataset.tone],
    ]),
  );

beforeEach(() => useAsyncDataMock.mockReset());
afterEach(cleanup);

describe("What the bot is doing", () => {
  it("shows a running bot, its target and today's count against the effective cap", () => {
    loaded(
      activity({
        trades_today: 1,
        recent_actions: [
          { at: "2026-10-27T10:42:00Z", outcome: "rejected", symbol: null, reason: "stop_too_wide" },
          { at: "2026-10-27T09:10:00Z", outcome: "entered", symbol: "BTCUSDT", direction: "long", journal_trade_id: TRADE },
        ],
      }),
    );
    render(<BotActivityPanel view={view} />);
    expect(screen.getByTestId("bot-activity-target")).toHaveTextContent("Nested BTC · BTCUSDT");
    expect(tiles()).toMatchObject({
      Bot: ["Running", "ok"],
      "Trades today": ["1 of 3", "ok"],
    });
    expect(tiles()["Last action"]![0]).toMatch(/^Rejected · /);
    expect(tiles()["Last action"]![1]).toBe("down");
    const list = screen.getByText("Last 2 actions").closest("details")!;
    expect(list).not.toHaveAttribute("open");
    expect(within(list).getByText(/Rejected · Stop too wide/)).toBeInTheDocument();
    expect(within(list).getByText(/Entered · BTCUSDT Long/)).toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Trade" })).toHaveAttribute("href", `/journal?trade_id=${TRADE}`);
    expectNoIdentifiers();
  });

  it("goes amber when today's count reaches the effective cap", () => {
    loaded(activity({ trades_today: 3 }));
    render(<BotActivityPanel view={view} />);
    expect(tiles()["Trades today"]).toEqual(["3 of 3", "warn"]);
  });

  it("is honest when the bot is idle with nothing recorded", () => {
    loaded(activity({ trades_today: 0 }));
    render(<BotActivityPanel view={{ ...view, internal_paper_effective: false }} />);
    expect(tiles()).toMatchObject({
      Bot: ["Off", "unknown"],
      "Trades today": ["0 of 3", "ok"],
      "Last action": ["None yet", "unknown"],
    });
    expect(screen.queryByText(/Last \d+ actions/)).not.toBeInTheDocument();
  });

  it("marks a stale worker amber even when the switch is on", () => {
    loaded(activity());
    render(<BotActivityPanel view={{ ...view, worker_runtime_health: "STALE", worker_flags_stale: true }} />);
    expect(tiles().Bot).toEqual(["Stale", "warn"]);
  });

  it("says when no strategy or coin is set and the count is unknown", () => {
    loaded(activity({ trades_today: null }));
    render(<BotActivityPanel view={{ ...view, strategy_id: null, symbol: null }} />);
    expect(screen.getByTestId("bot-activity-target")).toHaveTextContent("No strategy or coin set");
    expect(tiles()["Trades today"]).toEqual(["Unknown", "unknown"]);
  });

  it("reports unavailable activity without inventing numbers", () => {
    loaded(null, { error: "Not Found" });
    render(<BotActivityPanel view={view} />);
    expect(screen.getByText("Bot activity unavailable.")).toBeInTheDocument();
    expect(tiles()["Trades today"]).toEqual(["Unknown", "unknown"]);
    expect(tiles()["Last action"]).toEqual(["None yet", "unknown"]);
  });

  it("reports a missing worker as not reported", () => {
    expect(botState({ ...view, worker_runtime_health: undefined })).toEqual({ tone: "unknown", state: "Not reported" });
    expect(botState({ ...view, worker_runtime_health: "UNAVAILABLE" })).toEqual({ tone: "unknown", state: "Not reported" });
  });
});
