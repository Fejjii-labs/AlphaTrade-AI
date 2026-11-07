import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { StatusBadge } from "@/components/StatusBadge";

describe("Market status UI", () => {
  it("renders live market badge", () => {
    render(<StatusBadge label="Live market data" tone="healthy" />);
    expect(screen.getByText("Live market data")).toBeInTheDocument();
  });

  it("renders mock market badge", () => {
    render(<StatusBadge label="Mock market data" tone="paper" />);
    expect(screen.getByText("Mock market data")).toBeInTheDocument();
  });
});
