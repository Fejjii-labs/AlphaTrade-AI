import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import SettingsExchangePage from "./page";

vi.mock("@/components/exchange/ExchangeStatusCard", () => ({
  ExchangeStatusCard: () => <div data-testid="exchange-status-card">card</div>,
}));

afterEach(cleanup);

describe("Settings > Exchange", () => {
  it("renders the title and the single status card at once, with no diagnostics block", () => {
    render(<SettingsExchangePage />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent(/^Exchange$/);
    expect(screen.getByTestId("exchange-status-card")).toBeInTheDocument();
    expect(screen.queryByTestId("loading-state")).not.toBeInTheDocument();
    for (const gone of [/diagnostics/i, /Demo account sync/, /BloFin demo sync/, /Worker/]) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
  });
});
