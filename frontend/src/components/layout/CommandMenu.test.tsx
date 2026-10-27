import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CommandMenu } from "@/components/layout/CommandMenu";

/**
 * FP2-202 — the command menu must be operable as a combobox + listbox: arrow
 * keys move the active option, Home/End jump to the ends, and Enter activates
 * without the pointer.
 */
describe("CommandMenu keyboard interaction (FP2-202)", () => {
  afterEach(() => cleanup());

  function openMenu() {
    const onOpenChange = vi.fn();
    render(<CommandMenu open onOpenChange={onOpenChange} />);
    return {
      onOpenChange,
      input: screen.getByRole("combobox", { name: "Filter command menu" }),
      options: () => screen.getAllByRole("option"),
    };
  }

  it("exposes combobox and listbox semantics", () => {
    const { input } = openMenu();
    const listbox = screen.getByRole("listbox", { name: "Command results" });
    expect(input).toHaveAttribute("aria-expanded", "true");
    expect(input).toHaveAttribute("aria-autocomplete", "list");
    expect(input).toHaveAttribute("aria-controls", listbox.id);
  });

  it("shows the seven primary destinations first without duplicating Knowledge", () => {
    const { options } = openMenu();
    expect(
      options()
        .slice(0, 7)
        .map((option) => option.getAttribute("href")),
    ).toEqual(["/", "/autonomous", "/strategies", "/journal", "/agent", "/knowledge", "/settings"]);
    expect(
      options().filter(
        (option) => option.getAttribute("href") === "/knowledge",
      ),
    ).toHaveLength(1);
    expect(
      options().some(
        (option) => option.getAttribute("href") === "/strategy-lab",
      ),
    ).toBe(true);
  });

  it("marks the first option active and tracks it with aria-activedescendant", () => {
    const { input, options } = openMenu();
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
    expect(input).toHaveAttribute("aria-activedescendant", options()[0].id);
  });

  it("moves the active option with ArrowDown and ArrowUp, wrapping at both ends", () => {
    const { input, options } = openMenu();
    const total = options().length;
    expect(total).toBeGreaterThan(2);

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(options()[1]).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(options()[0]).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(input, { key: "ArrowUp" });
    expect(options()[total - 1]).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(input, { key: "ArrowDown" });
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
  });

  it("jumps to the first and last option with Home and End", () => {
    const { input, options } = openMenu();
    const total = options().length;

    fireEvent.keyDown(input, { key: "End" });
    expect(options()[total - 1]).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(input, { key: "Home" });
    expect(options()[0]).toHaveAttribute("aria-selected", "true");
  });

  it("activates the highlighted option with Enter and closes the menu", () => {
    const { input, options, onOpenChange } = openMenu();
    fireEvent.keyDown(input, { key: "ArrowDown" });
    const target = options()[1];
    const click = vi.spyOn(target, "click");

    fireEvent.keyDown(input, { key: "Enter" });

    expect(click).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it("resets the active option to the first match when the query changes", () => {
    const { input, options } = openMenu();
    fireEvent.keyDown(input, { key: "End" });
    fireEvent.change(input, { target: { value: "port" } });

    const matches = options();
    expect(matches.length).toBeGreaterThan(0);
    expect(matches[0]).toHaveAttribute("aria-selected", "true");
  });

  it("keeps keyboard handling inert when nothing matches", () => {
    const { input } = openMenu();
    fireEvent.change(input, { target: { value: "zzzz-no-such-destination" } });
    expect(screen.queryAllByRole("option")).toHaveLength(0);
    expect(screen.getByText("No matches")).toBeInTheDocument();

    fireEvent.keyDown(input, { key: "Enter" });
    expect(input).not.toHaveAttribute("aria-activedescendant");
  });

  it("gives every option a 44 px minimum touch target", () => {
    const { options } = openMenu();
    for (const option of options()) {
      expect(option.className).toContain("min-h-11");
    }
  });
});
