import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { SegmentedControl } from "@/components/ui/segmented-control";

function Range({ initial = "7d" }: { initial?: string }) {
  const [value, setValue] = useState(initial);
  return (
    <SegmentedControl
      aria-label="Range"
      value={value}
      onChange={setValue}
      options={[
        { value: "7d", label: "7D" },
        { value: "30d", label: "30D", disabled: true },
        { value: "all", label: "All" },
      ]}
    />
  );
}

describe("SegmentedControl keyboard", () => {
  it("selects the next enabled option with ArrowRight and wraps", () => {
    render(<Range />);
    const group = screen.getByRole("radiogroup", { name: "Range" });
    const seven = screen.getByRole("radio", { name: "7D" });
    seven.focus();
    fireEvent.keyDown(group, { key: "ArrowRight" });
    const all = screen.getByRole("radio", { name: "All" });
    expect(all).toHaveAttribute("aria-checked", "true");
    expect(all).toHaveFocus();
    expect(screen.getByRole("radio", { name: "30D" })).toBeDisabled();
    fireEvent.keyDown(group, { key: "ArrowRight" });
    expect(seven).toHaveAttribute("aria-checked", "true");
  });

  it("supports Home, End, and click", () => {
    render(<Range />);
    const group = screen.getByRole("radiogroup", { name: "Range" });
    screen.getByRole("radio", { name: "7D" }).focus();
    fireEvent.keyDown(group, { key: "End" });
    expect(screen.getByRole("radio", { name: "All" })).toHaveAttribute("aria-checked", "true");
    fireEvent.keyDown(group, { key: "Home" });
    expect(screen.getByRole("radio", { name: "7D" })).toHaveAttribute("aria-checked", "true");
    fireEvent.click(screen.getByRole("radio", { name: "All" }));
    expect(screen.getByRole("radio", { name: "All" })).toHaveAttribute("aria-checked", "true");
  });

  it("paints demo and paper with their mode colours", () => {
    render(
      <SegmentedControl
        aria-label="Account"
        value="demo"
        onChange={() => undefined}
        options={[
          { value: "demo", label: "Demo", tone: "demo" },
          { value: "simulator", label: "Simulator", tone: "paper" },
        ]}
      />,
    );
    expect(screen.getByRole("radio", { name: "Demo" }).className).toContain("text-mode-demo");
    expect(screen.getByRole("radio", { name: "Simulator" }).className).not.toContain("text-mode-paper");
  });
});
