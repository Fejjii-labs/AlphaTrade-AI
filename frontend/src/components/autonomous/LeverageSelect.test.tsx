import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { leverageChoices, LeverageSelect } from "./LeverageSelect";

afterEach(cleanup);

const labels = () =>
  [...screen.getByRole("combobox", { name: "Leverage" }).querySelectorAll("option")].map((o) => o.textContent);

describe("LeverageSelect", () => {
  it("always lists the 2x, 3x, 5x and 10x presets, none disabled, no cap text", () => {
    expect(leverageChoices(3)).toEqual([2, 3, 5, 10]);
    render(<LeverageSelect value={3} onChange={() => {}} />);
    expect(labels()).toEqual(["2×", "3×", "5×", "10×"]);
    const options = [...screen.getByRole("combobox", { name: "Leverage" }).querySelectorAll("option")];
    expect(options.some((o) => o.disabled)).toBe(false);
    expect(screen.queryByText(/above max/)).toBeNull();
  });

  it("keeps a saved non-preset value selected alongside the presets", () => {
    expect(leverageChoices(1)).toEqual([1, 2, 3, 5, 10]);
    expect(leverageChoices(7)).toEqual([2, 3, 5, 7, 10]);
    render(<LeverageSelect value={7} onChange={() => {}} />);
    expect(screen.getByRole("combobox", { name: "Leverage" })).toHaveValue("7");
    expect(labels()).toEqual(["2×", "3×", "5×", "7×", "10×"]);
  });

  it("shows an unknown value as a dash", () => {
    render(<LeverageSelect value={null} onChange={() => {}} />);
    expect(screen.getByRole("combobox", { name: "Leverage" })).toHaveValue("");
    expect(labels()).toEqual(["—", "2×", "3×", "5×", "10×"]);
  });

  it("reports the picked number", () => {
    const onChange = vi.fn();
    render(<LeverageSelect value={2} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Leverage" }), { target: { value: "10" } });
    expect(onChange).toHaveBeenCalledWith(10);
  });
});
