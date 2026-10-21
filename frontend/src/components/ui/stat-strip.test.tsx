import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UNAVAILABLE } from "@/lib/format";
import { Stat, StatStrip } from "@/components/ui/stat-strip";

describe("StatStrip", () => {
  it("shows a mono value and a signed positive delta", () => {
    render(
      <StatStrip label="Overview">
        <Stat label="Today PnL" value="+$84.20" delta={84.2} deltaLabel="+$84.20" />
      </StatStrip>,
    );
    expect(screen.getByTestId("stat-strip")).toHaveAttribute("aria-label", "Overview");
    expect(screen.getByText("Today PnL")).toBeInTheDocument();
    expect(screen.getByText("+$84.20")).toBeInTheDocument();
    expect(screen.getByText("▲ +$84.20")).toBeInTheDocument();
  });

  it("renders an unavailable dash instead of zero", () => {
    render(
      <StatStrip>
        <Stat label="Equity" unavailable delta={null} />
      </StatStrip>,
    );
    const dashes = screen.getAllByText(UNAVAILABLE);
    expect(dashes.length).toBeGreaterThanOrEqual(1);
    expect(dashes[0]).toHaveAttribute("title", "Not available yet");
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });

  it("shows a loss delta with a down marker", () => {
    render(
      <StatStrip>
        <Stat label="Today PnL" value="−$12.00" delta={-12} deltaLabel="−$12.00" />
      </StatStrip>,
    );
    expect(screen.getByText("▼ −$12.00")).toBeInTheDocument();
  });
});
