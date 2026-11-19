import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { leverageChoices, LeverageSelect } from "./LeverageSelect";

afterEach(cleanup);

const ONE_TO_TEN = ["1×", "2×", "3×", "4×", "5×", "6×", "7×", "8×", "9×", "10×"];

describe("LeverageSelect", () => {
  it("always offers 1x to 10x, all choosable", () => {
    expect(leverageChoices()).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    render(<LeverageSelect value={3} onChange={() => {}} />);
    const options = [...screen.getByRole("combobox", { name: "Leverage" }).querySelectorAll("option")];
    expect(options.map((o) => o.textContent)).toEqual(ONE_TO_TEN);
    expect(options.some((o) => o.disabled)).toBe(false);
    expect(screen.queryByText(/above max/)).toBeNull();
  });

  it("shows a saved value outside 1..10 as unset", () => {
    render(<LeverageSelect value={15} onChange={() => {}} />);
    const select = screen.getByRole("combobox", { name: "Leverage" }) as HTMLSelectElement;
    expect(select.value).toBe("");
    expect([...select.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["—", ...ONE_TO_TEN]);
  });

  it("reports the picked number", () => {
    const onChange = vi.fn();
    render(<LeverageSelect value={2} onChange={onChange} />);
    fireEvent.change(screen.getByRole("combobox", { name: "Leverage" }), { target: { value: "10" } });
    expect(onChange).toHaveBeenCalledWith(10);
  });
});
