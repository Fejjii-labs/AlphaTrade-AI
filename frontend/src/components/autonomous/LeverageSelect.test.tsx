import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { leverageChoices, LeverageSelect } from "./LeverageSelect";

afterEach(cleanup);

describe("LeverageSelect", () => {
  it("offers whole numbers up to max and keeps a saved value above max visible but not choosable", () => {
    expect(leverageChoices(3, 5)).toEqual([1, 2, 3, 4, 5]);
    expect(leverageChoices(10, 1)).toEqual([1, 10]);
    render(<LeverageSelect value={10} max={1} onChange={() => {}} />);
    const options = [...screen.getByRole("combobox", { name: "Leverage" }).querySelectorAll("option")];
    expect(options.map((o) => [o.textContent, o.disabled])).toEqual([["1×", false], ["10× (above max)", true]]);
  });

  it("reports the picked number", () => {
    const onChange = vi.fn();
    render(<LeverageSelect value={2} max={5} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Leverage" }), { target: { value: "4" } });
    expect(onChange).toHaveBeenCalledWith(4);
  });
});
