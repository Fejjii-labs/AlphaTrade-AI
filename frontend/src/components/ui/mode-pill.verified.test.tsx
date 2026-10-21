import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/contexts/AppContext", () => ({
  useSafetyPosture: () => ({
    executionMode: null,
    realTradingEnabled: null,
    providerMode: "fallback",
    postureKnown: false,
  }),
}));

import { VerifiedModePill } from "@/components/ui/mode-pill";

describe("VerifiedModePill", () => {
  it("stays unverified until health posture is known", () => {
    render(<VerifiedModePill />);
    const pill = screen.getByTestId("mode-pill");
    expect(pill).toHaveAttribute("data-kind", "unverified");
    expect(pill).not.toHaveTextContent("PAPER");
  });
});
