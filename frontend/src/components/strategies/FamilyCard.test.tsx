import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { BrainSetup } from "@/lib/api/brain-types";
import { expectNoIdentifiers } from "@/test/no-identifiers";
import { dailyCounts, FamilyCard } from "./FamilyCard";
import { StrategyRules } from "./StrategyRules";

afterEach(cleanup);

const NOW = new Date(2026, 9, 27, 12, 0);
const setup = (i: number, daysAgo: number): BrainSetup =>
  ({
    setup_id: `0000000${i}-0000-4000-8000-000000000000`,
    family: "nested_continuation",
    symbol: "BTCUSDT",
    instrument: "BTCUSDT",
    direction: "long",
    timeframe: "15m",
    state: "CONFIRMED",
    stage: "N2_CONFIRMED",
    observed_at: new Date(NOW.getTime() - daysAgo * 86400000 - i * 60000).toISOString(),
  }) as unknown as BrainSetup;

describe("FamilyCard", () => {
  it("shows the 7-day count, bars, the latest three and the rest behind Show all", () => {
    const setups = [setup(1, 0), setup(2, 0), setup(3, 1), setup(4, 3), setup(5, 6)];
    render(<FamilyCard id="nested" name="Nested Continuation" loading={false} versions={2} detections={{ setups, count: 5 }} />);
    const card = screen.getByTestId("strategy-family");
    expect(within(card).getByTestId("family-count")).toHaveTextContent("5 in 7 days");
    expect(within(card).getByText("2 versions · Paper")).toBeInTheDocument();
    expect(within(card).getByTestId("family-bars").children).toHaveLength(7);
    const visible = within(card).getAllByRole("link").filter((link) => link.getAttribute("href")?.startsWith("/strategies/setups/"));
    expect(visible).toHaveLength(5);
    expect(visible[0]).toHaveTextContent("BTCUSDT · Long · 15m · N2 confirmed");
    const all = within(card).getByText("Show all 7 days").closest("details")!;
    expect(all).not.toHaveAttribute("open");
    expect(within(all).getAllByRole("link")).toHaveLength(2);
    expect(within(card).getByRole("link", { name: "Ask Agent" })).toHaveAttribute("href", "/agent?discuss=nested");
    expect(within(card).getByRole("link", { name: "Versions" })).toHaveAttribute("href", "/strategies?family=nested");
    expectNoIdentifiers();
  });

  it("says when a family has no detections or cannot be read", () => {
    render(<FamilyCard id="sfp" name="SFP" loading={false} versions={null} detections={{ setups: [], count: 0 }} />);
    expect(screen.getByText("No detections in 7 days.")).toBeInTheDocument();
    expect(screen.getByText("Versions unavailable")).toBeInTheDocument();
    expect(screen.queryByText("Show all 7 days")).not.toBeInTheDocument();
    expect(screen.queryByTestId("family-bars")).not.toBeInTheDocument();
    cleanup();
    render(<FamilyCard id="sfp" name="SFP" loading={false} versions={0} detections={null} />);
    expect(screen.getByTestId("family-count")).toHaveTextContent("Detections unavailable");
    expect(screen.queryByTestId("family-bars")).not.toBeInTheDocument();
  });

  it("counts detections per local day, oldest first", () => {
    const counts = dailyCounts([setup(1, 0), setup(2, 0), setup(3, 2), setup(4, 9)], NOW).map((d) => d.count);
    expect(counts).toEqual([0, 0, 0, 0, 1, 0, 2]);
  });
});

describe("StrategyRules", () => {
  it("lists readable rules without raw JSON, IDs or schema markers", () => {
    render(
      <StrategyRules
        spec={{
          kind: "swing_failure_pattern/v1",
          symbol: "BTCUSDT",
          direction: "long",
          trigger_timeframe: "15m",
          parameters: { version: "sfp-research/v1", provisional: true, reclaim_window: 3, confirmation: "closed_break_of_reclaim_extreme", nested: { a: 1 }, strategy_id: "11111111-2222-4333-8444-555555555555" },
        }}
      />,
    );
    const rules = screen.getByTestId("strategy-rules");
    expect(rules).toHaveTextContent("Symbol");
    expect(rules).toHaveTextContent("Reclaim window3");
    expect(rules).toHaveTextContent("closed break of reclaim extreme");
    expect(rules).not.toHaveTextContent("{");
    expect(rules).not.toHaveTextContent("sfp-research");
    expectNoIdentifiers();
  });
});
