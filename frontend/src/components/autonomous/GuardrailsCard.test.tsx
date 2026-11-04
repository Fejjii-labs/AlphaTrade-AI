import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { autonomousBounds } from "@/test/autonomous-bounds-fixtures";

import { GuardrailsCard } from "./GuardrailsCard";

afterEach(cleanup);

describe("GuardrailsCard", () => {
  it("shows the configured stop-loss band and ceilings from the server, read-only", () => {
    render(<GuardrailsCard bounds={autonomousBounds} />);
    const card = screen.getByTestId("guardrails-card");
    expect(card).toHaveAttribute("id", "guardrails");
    expect(screen.getByTestId("guardrails-stop-loss-band")).toHaveTextContent("1%–5% of equity per trade");
    expect(card).toHaveTextContent("Bot risk per tradeMax 5%");
    expect(card).toHaveTextContent("Bot daily loss limitMax 6%");
    expect(card).toHaveTextContent("Account risk per tradeMax 10%");
    expect(card).toHaveTextContent("Demo max 20×");
    expect(card).toHaveTextContent("BloFin demo profile");
    expect(card).toHaveTextContent("≤ 2000% of equity");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("guardrails-band-invalid")).not.toBeInTheDocument();
  });

  it("renders whatever band the server reports, and flags one outside the allowed range", () => {
    render(
      <GuardrailsCard
        bounds={{
          ...autonomousBounds,
          stop_loss_band: { ...autonomousBounds.stop_loss_band, configured_min_percent: "2", configured_max_percent: "8", configured_valid: false },
        }}
      />,
    );
    expect(screen.getByTestId("guardrails-stop-loss-band")).toHaveTextContent("2%–8% of equity per trade");
    expect(screen.getByTestId("guardrails-band-invalid")).toHaveTextContent("demo plans are refused");
  });

  it("says the ceilings are unavailable without bounds", () => {
    render(<GuardrailsCard bounds={null} />);
    expect(screen.getByTestId("guardrails-unavailable")).toHaveTextContent("server still enforces");
  });
});
