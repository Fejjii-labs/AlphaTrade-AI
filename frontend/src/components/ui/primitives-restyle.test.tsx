import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RiskBadge } from "@/components/RiskBadge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardFooter } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/ui/page-header";
import { StatStripSkeleton, TableSkeleton } from "@/components/ui/skeleton";
import { LoadingState } from "@/components/states";

describe("phase 2 primitive restyle", () => {
  it("paints the primary button with the solid accent and white text", () => {
    render(<Button>Save</Button>);
    const button = screen.getByRole("button", { name: "Save" });
    expect(button.className).toContain("bg-accent-solid");
    expect(button.className).toContain("text-white");
  });

  it("keeps cards flat and supports an inset variant plus a footer", () => {
    render(
      <Card variant="inset" data-testid="card">
        <CardFooter>Footer</CardFooter>
      </Card>,
    );
    const card = screen.getByTestId("card");
    expect(card.className).toContain("bg-surface-0");
    expect(card.className).not.toContain("shadow");
    expect(screen.getByText("Footer")).toBeInTheDocument();
  });

  it("separates paper, demo, and profit badge colours", () => {
    const { rerender } = render(<Badge variant="paper">PAPER</Badge>);
    const paper = screen.getByText("PAPER").className;
    rerender(<Badge variant="demo">DEMO</Badge>);
    const demo = screen.getByText("DEMO").className;
    rerender(<Badge variant="success">Up</Badge>);
    const profit = screen.getByText("Up").className;
    expect(paper).not.toBe(demo);
    expect(paper).not.toBe(profit);
    expect(demo).not.toBe(profit);
    expect(paper).toContain("text-mode-paper");
    expect(demo).toContain("text-mode-demo");
    expect(profit).toContain("text-success");
  });

  it("remaps risk levels onto distinct labels and colours", () => {
    const { rerender } = render(<RiskBadge level={null} />);
    expect(screen.getByText("Risk unknown")).toBeInTheDocument();
    rerender(<RiskBadge level="low" />);
    expect(screen.getByText("Risk low").className).toContain("text-risk-low");
    rerender(<RiskBadge level="high" />);
    expect(screen.getByText("Risk high").className).toContain("text-risk-high");
    rerender(<RiskBadge level="critical" />);
    expect(screen.getByText("Risk critical").className).toContain("bg-risk-critical");
  });

  it("puts inputs on surface 2", () => {
    render(<Input aria-label="Search" />);
    expect(screen.getByRole("textbox", { name: "Search" }).className).toContain("bg-surface-2");
  });

  it("adds an eyebrow to the page header without dropping the title", () => {
    render(<PageHeader eyebrow="Overview" title="Dashboard" description="Your trading day" />);
    expect(screen.getByTestId("eyebrow")).toHaveTextContent("Overview");
    expect(screen.getByRole("heading", { level: 1, name: "Dashboard" })).toBeInTheDocument();
  });

  it("offers stat-strip and table loading presets", () => {
    const { rerender } = render(<LoadingState preset="stat-strip" label="Loading dashboard…" />);
    expect(screen.getByText("Loading dashboard…")).toBeInTheDocument();
    expect(screen.getByTestId("stat-strip-skeleton")).toBeInTheDocument();
    rerender(<LoadingState preset="table" />);
    expect(screen.getByTestId("table-skeleton")).toBeInTheDocument();
    render(<StatStripSkeleton cells={2} />);
    render(<TableSkeleton rows={2} columns={3} />);
  });
});
