// Separate from RiskProfileSections.test.tsx, which PR #430 edits.
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AutonomousTradingSettingsView } from "@/lib/api/types";

import { RiskProfileSections } from "./RiskProfileSections";

vi.mock("@/lib/api", () => ({ api: { riskPolicy: { profile: () => Promise.reject(new Error("403")) } } }));
vi.mock("@/components/autonomous/LeverageSettingsSlot", () => ({
  LeverageSettingsSlot: () => <section data-testid="pr430-leverage-editor" />,
}));

afterEach(cleanup);

describe("RiskProfileSections with the page's settings", () => {
  it("shows the pending leverage section when the settings have no demo leverage", async () => {
    render(<RiskProfileSections settings={{} as AutonomousTradingSettingsView} />);
    expect(await screen.findByTestId("leverage-pending")).toHaveTextContent("pending PR #430");
    expect(screen.queryByTestId("pr430-leverage-editor")).not.toBeInTheDocument();
  });

  it("renders the leverage slot as before when no settings are passed", async () => {
    render(<RiskProfileSections />);
    expect(await screen.findByTestId("pr430-leverage-editor")).toBeInTheDocument();
  });
});
