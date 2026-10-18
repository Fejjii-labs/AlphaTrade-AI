import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { UNAVAILABLE } from "@/lib/format";
import { Gauge } from "@/components/ui/gauge";

describe("Gauge", () => {
  it("shows the number and the label", () => {
    render(<Gauge value={42.2} label="Discipline" />);
    const meter = screen.getByRole("meter", { name: "Discipline" });
    expect(meter).toHaveAttribute("aria-valuenow", "42.2");
    expect(meter).toHaveAttribute("aria-valuetext", "42.2 out of 100");
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("Discipline")).toBeInTheDocument();
  });

  it("clamps values into 0–100", () => {
    render(<Gauge value={140} label="Quality" />);
    expect(screen.getByRole("meter")).toHaveAttribute("aria-valuenow", "100");
    expect(screen.getByText("100")).toBeInTheDocument();
  });

  it("shows a dash instead of a fake zero", () => {
    render(<Gauge value={null} label="Discipline" />);
    const meter = screen.getByRole("meter", { name: "Discipline" });
    expect(meter).not.toHaveAttribute("aria-valuenow");
    expect(meter).toHaveAttribute("aria-valuetext", "Not available yet");
    expect(screen.getByText(UNAVAILABLE)).toBeInTheDocument();
    expect(screen.queryByText("0")).not.toBeInTheDocument();
  });
});
