import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UNAVAILABLE } from "@/lib/format";
import { Sparkline } from "@/components/ui/sparkline";

describe("Sparkline", () => {
  it("draws a trend without axes", () => {
    const { container } = render(<Sparkline label="Equity 7 day" values={[1, 3, 2]} />);
    expect(screen.getByRole("img", { name: "Equity 7 day" })).toBeInTheDocument();
    expect(container.querySelector("svg")).not.toBeNull();
    expect(container.querySelector(".recharts-xAxis, .recharts-yAxis")).toBeNull();
  });

  it("shows an unavailable dash when there is no series", () => {
    render(<Sparkline label="Equity 7 day" values={[]} />);
    const spark = screen.getByTestId("sparkline");
    expect(spark).toHaveTextContent(UNAVAILABLE);
    expect(spark).toHaveAttribute("aria-label", "Equity 7 day: not available yet");
  });

  it("keeps loss colour off a neutral series", () => {
    render(<Sparkline label="Watch" values={[4, 4]} tone="neutral" />);
    expect(screen.getByTestId("sparkline").className).toContain("text-accent");
    expect(screen.getByTestId("sparkline").className).not.toContain("text-positive");
  });
});
