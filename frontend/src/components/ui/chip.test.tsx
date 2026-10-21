import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { Chip, FilterChips } from "@/components/ui/chip";

function Filters({ selection = "multiple" as "single" | "multiple" }) {
  const [value, setValue] = useState<string[]>([]);
  return (
    <FilterChips
      aria-label="Trade filters"
      selection={selection}
      value={value}
      onChange={setValue}
      options={[
        { id: "all", label: "All" },
        { id: "winners", label: "Winners", count: 3 },
        { id: "off", label: "Off", disabled: true },
        { id: "losers", label: "Losers", count: 1 },
      ]}
    />
  );
}

describe("Chip", () => {
  it("toggles pressed state and shows the count", () => {
    function Harness() {
      const [pressed, setPressed] = useState(false);
      return <Chip label="Needs notes" count={3} pressed={pressed} onPressedChange={setPressed} />;
    }
    render(<Harness />);
    const chip = screen.getByRole("button", { name: "Needs notes 3" });
    expect(chip).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(chip);
    expect(chip).toHaveAttribute("aria-pressed", "true");
  });
});

describe("FilterChips keyboard", () => {
  it("moves focus with arrows, skips disabled chips, and toggles with Space", () => {
    render(<Filters />);
    const toolbar = screen.getByRole("toolbar", { name: "Trade filters" });
    const all = screen.getByRole("button", { name: "All" });
    all.focus();
    fireEvent.keyDown(toolbar, { key: "ArrowRight" });
    const winners = screen.getByRole("button", { name: "Winners 3" });
    expect(winners).toHaveFocus();
    fireEvent.keyDown(toolbar, { key: "ArrowRight" });
    expect(screen.getByRole("button", { name: "Losers 1" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Off" })).toBeDisabled();
    fireEvent.keyDown(toolbar, { key: " " });
    expect(screen.getByRole("button", { name: "Losers 1" })).toHaveAttribute("aria-pressed", "true");
    fireEvent.keyDown(toolbar, { key: "Home" });
    expect(all).toHaveFocus();
  });

  it("keeps a single selection when asked", () => {
    render(<Filters selection="single" />);
    fireEvent.click(screen.getByRole("button", { name: "All" }));
    fireEvent.click(screen.getByRole("button", { name: "Winners 3" }));
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByRole("button", { name: "Winners 3" })).toHaveAttribute("aria-pressed", "true");
  });
});
