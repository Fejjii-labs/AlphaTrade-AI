import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { leverageChoices, LeverageSelect } from "./LeverageSelect";

afterEach(cleanup);

const labels = () =>
  [...screen.getByRole("combobox", { name: "Leverage" }).querySelectorAll("option")].map((o) => o.textContent);

const choices = Array.from({ length: 10 }, (_, i) => i + 1);
const choiceLabels = choices.map((value) => `${value}×`);

describe("LeverageSelect", () => {
  it("always lists 1x through 10x, none disabled, no cap text", () => {
    expect(leverageChoices(3)).toEqual(choices);
    render(<LeverageSelect value={3} onChange={() => {}} />);
    expect(labels()).toEqual(choiceLabels);
    const options = [...screen.getByRole("combobox", { name: "Leverage" }).querySelectorAll("option")];
    expect(options.some((o) => o.disabled)).toBe(false);
    expect(screen.queryByText(/above max/)).toBeNull();
  });

  it("keeps a saved non-preset value selected alongside the presets", () => {
    expect(leverageChoices(1)).toEqual(choices);
    expect(leverageChoices(12)).toEqual([...choices, 12]);
    render(<LeverageSelect value={12} onChange={() => {}} />);
    expect(screen.getByRole("combobox", { name: "Leverage" })).toHaveValue("12");
    expect(labels()).toEqual([...choiceLabels, "12×"]);
  });

  it("shows an unknown value as a dash", () => {
    render(<LeverageSelect value={null} onChange={() => {}} />);
    expect(screen.getByRole("combobox", { name: "Leverage" })).toHaveValue("");
    expect(labels()).toEqual(["—", ...choiceLabels]);
  });

  it("reports the picked number", () => {
    const onChange = vi.fn();
    render(<LeverageSelect value={2} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Leverage" }), { target: { value: "10" } });
    expect(onChange).toHaveBeenCalledWith(10);
  });
});
