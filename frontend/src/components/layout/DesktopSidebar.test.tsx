import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  DesktopSidebar,
  SIDEBAR_COLLAPSED_KEY,
} from "@/components/layout/DesktopSidebar";
import { PRIMARY_DESTINATIONS } from "@/components/layout/navigation-config";

vi.mock("next/navigation", () => ({
  usePathname: () => "/journal",
}));

vi.mock("@/contexts/AppContext", () => ({
  useSafetyPosture: () => ({
    executionMode: "paper",
    realTradingEnabled: false,
    providerMode: "mock",
    postureKnown: true,
  }),
}));

describe("AT-040 collapsible DesktopSidebar", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => cleanup());

  it("defaults expanded with five destinations and accessible labels", () => {
    const onOpen = vi.fn();
    render(<DesktopSidebar onOpenCommandMenu={onOpen} />);
    const sidebar = screen.getByTestId("desktop-sidebar");
    expect(sidebar).toHaveAttribute("data-collapsed", "false");
    expect(sidebar.className).toContain("w-60");
    const nav = within(sidebar).getByRole("navigation", {
      name: "Primary destinations",
    });
    expect(within(nav).getAllByRole("link")).toHaveLength(7);
    expect(
      within(nav)
        .getAllByRole("link")
        .map((link) => link.getAttribute("aria-label")),
    ).toEqual([
      "Dashboard",
      "Autonomous",
      "Strategies",
      "Trades",
      "Agent",
      "Knowledge",
      "Settings",
    ]);
    expect(
      within(nav).getByRole("link", { name: "Trades" }),
    ).toHaveAttribute("aria-current", "page");
    expect(nav.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
    expect(within(nav).getByRole("link", { name: "Agent" })).not.toHaveClass(
      "border-accent-border",
      "text-accent",
    );
    fireEvent.click(screen.getByTestId("sidebar-command-menu"));
    expect(onOpen).toHaveBeenCalled();
  });

  it("collapses to icon rail, persists preference, and keeps accessible names", async () => {
    render(<DesktopSidebar onOpenCommandMenu={vi.fn()} />);
    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));
    const sidebar = screen.getByTestId("desktop-sidebar");
    expect(sidebar).toHaveAttribute("data-collapsed", "true");
    expect(sidebar.className).toContain("w-16");
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBe("1");
    expect(screen.getByRole("link", { name: "Dashboard" })).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Trades" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Trades" }),
    ).toHaveAttribute("aria-current", "page");
    expect(screen.getByTestId("sidebar-command-menu")).toHaveAttribute(
      "aria-label",
      "Open command menu",
    );
    expect(
      screen.queryByTestId("sidebar-environment-chip"),
    ).not.toBeInTheDocument();

    cleanup();
    render(<DesktopSidebar onOpenCommandMenu={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByTestId("desktop-sidebar")).toHaveAttribute(
        "data-collapsed",
        "true",
      );
    });
  });

  it("expands again and persists expanded state", () => {
    localStorage.setItem(SIDEBAR_COLLAPSED_KEY, "1");
    render(<DesktopSidebar onOpenCommandMenu={vi.fn()} />);
    fireEvent.click(screen.getByTestId("sidebar-collapse-toggle"));
    expect(screen.getByTestId("desktop-sidebar")).toHaveAttribute(
      "data-collapsed",
      "false",
    );
    expect(localStorage.getItem(SIDEBAR_COLLAPSED_KEY)).toBe("0");
    for (const destination of PRIMARY_DESTINATIONS) {
      expect(
        screen.getByRole("link", { name: destination.ariaLabel }),
      ).toBeInTheDocument();
    }
  });
});
