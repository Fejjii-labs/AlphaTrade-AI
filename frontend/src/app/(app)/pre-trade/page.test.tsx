import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import PreTradePage from "@/app/(app)/pre-trade/page";

vi.mock("@/lib/api", () => ({
  api: {
    pretrade: { analyze: vi.fn() },
    risk: { lossAcceptance: vi.fn() },
  },
}));

describe("PreTradePage", () => {
  it("renders loss acceptance section label", () => {
    render(<PreTradePage />);
    expect(screen.getByText("Pre-Trade Analysis")).toBeInTheDocument();
  });

  it("links Human vs system to the real comparison page, not a placeholder", () => {
    render(<PreTradePage />);
    expect(screen.queryByText(/Placeholder panel/)).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Human vs system" })).toHaveAttribute("href", "/journal/comparison");
  });
});
