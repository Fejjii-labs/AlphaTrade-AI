import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { resolveSettingsSection, SETTINGS_SECTIONS } from "@/lib/navigation/settings-sections";
import SettingsLayout from "./layout";

const nav = vi.hoisted(() => ({ pathname: "/settings" }));
vi.mock("next/navigation", () => ({ usePathname: () => nav.pathname }));

afterEach(cleanup);

describe("Settings sub-nav", () => {
  it("lists the six sections in order", () => {
    render(<SettingsLayout><p>Body</p></SettingsLayout>);
    const subNav = screen.getByRole("navigation", { name: "Settings sections" });
    expect(within(subNav).getAllByRole("link").map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
      ["Account", "/settings"],
      ["Exchanges", "/settings/exchange"],
      ["Notifications", "/settings/notifications"],
      ["Watcher", "/settings/watcher"],
      ["Diagnostics", "/settings/advanced"],
      ["Help", "/settings/help"],
    ]);
    expect(screen.getByText("Body")).toBeInTheDocument();
  });

  it.each([
    ["/settings", "Account"],
    ["/settings/team", "Account"],
    ["/settings/billing", "Account"],
    ["/settings/exchange", "Exchanges"],
    ["/settings/notifications", "Notifications"],
    ["/settings/watcher", "Watcher"],
    ["/settings/advanced", "Diagnostics"],
    ["/settings/audit", "Diagnostics"],
    ["/settings/help", "Help"],
  ])("marks exactly one section current on %s", (pathname, label) => {
    nav.pathname = pathname;
    render(<SettingsLayout><p>Body</p></SettingsLayout>);
    const current = screen
      .getByRole("navigation", { name: "Settings sections" })
      .querySelectorAll('[aria-current="page"]');
    expect([...current].map((link) => link.textContent)).toEqual([label]);
  });

  it("is a horizontal scroll row on phones and a rail from lg up", () => {
    render(<SettingsLayout><p>Body</p></SettingsLayout>);
    const list = screen.getByRole("navigation", { name: "Settings sections" }).querySelector("ul")!;
    expect(list.className).toContain("overflow-x-auto");
    expect(list.className).toContain("lg:flex-col");
  });

  it("does not claim paths outside Settings", () => {
    expect(resolveSettingsSection("/settings-old")).toBeNull();
    expect(resolveSettingsSection("/strategies")).toBeNull();
    expect(SETTINGS_SECTIONS).toHaveLength(6);
  });
});
