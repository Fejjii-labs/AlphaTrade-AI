import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/layout/AppShell";
import { PRIMARY_DESTINATIONS } from "@/components/layout/navigation-config";

const posture = {
  executionMode: "paper" as string | null,
  realTradingEnabled: false as boolean | null,
  providerMode: "mock",
  postureKnown: true,
};

const navigationState = { pathname: "/tradingview-signals" };

vi.mock("next/navigation", () => ({
  usePathname: () => navigationState.pathname,
}));

vi.mock("@/contexts/AppContext", () => ({
  AppProvider: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  useAppContext: () => ({
    refreshStatus: vi.fn(),
    loading: false,
    killSwitchActive: false,
    killSwitchStatus: {
      organization_id: "org",
      active: false,
      reason: null,
      activated_by: null,
      activated_at: null,
      deactivated_by: null,
      deactivated_at: null,
      version: 1,
      scope: "organization",
      global_active: false,
      execution_blocked: false,
    },
    killSwitchError: null,
    health: { status: "ok" },
    providers: { providers: [] },
  }),
  useSafetyPosture: () => posture,
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { email: "trader@example.com" },
    organization: { name: "Alpha Org" },
    logout: vi.fn(),
  }),
}));

vi.mock("@/components/KillSwitchButton", () => ({
  KillSwitchButton: () => <button type="button">Kill</button>,
}));

vi.mock("@/components/RiskBadge", () => ({
  RiskBadge: () => <span>Risk</span>,
}));

describe("AT-040 Phase B AppShell", () => {
  beforeEach(() => {
    posture.executionMode = "paper";
    posture.realTradingEnabled = false;
    navigationState.pathname = "/tradingview-signals";
  });

  afterEach(() => cleanup());

  it("renders seven desktop primary destinations and highlights Settings on retained routes", () => {
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    const sidebar = screen.getByTestId("desktop-sidebar");
    const links = within(sidebar).getAllByRole("link");
    expect(links).toHaveLength(7);
    for (const destination of PRIMARY_DESTINATIONS) {
      expect(
        within(sidebar).getByRole("link", { name: destination.ariaLabel }),
      ).toHaveAttribute("href", destination.href);
    }
    expect(
      within(sidebar).getByRole("link", { name: "Settings" }),
    ).toHaveAttribute("aria-current", "page");
  });

  it("renders four phone tabs plus More, with Autonomous, Knowledge and Settings in its sheet", () => {
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    const bottom = screen.getByTestId("mobile-bottom-navigation");
    expect(
      within(bottom)
        .getAllByRole("link")
        .map((link) => [link.textContent, link.getAttribute("href")]),
    ).toEqual([
      ["Dashboard", "/"],
      ["Trades", "/journal"],
      ["Strategies", "/strategies"],
      ["Agent", "/agent"],
    ]);
    const more = within(bottom).getByRole("button", { name: "More" });
    // /tradingview-signals belongs to Settings, which sits behind More.
    expect(more).toHaveAttribute("data-active", "true");
    expect(bottom.querySelectorAll('[aria-current="page"]')).toHaveLength(0);
    fireEvent.click(more);
    const sheet = screen.getByRole("dialog", { name: "More" });
    expect(
      within(sheet)
        .getAllByRole("link")
        .map((link) => [link.textContent, link.getAttribute("href")]),
    ).toEqual([
      ["Autonomous", "/autonomous"],
      ["Knowledge", "/knowledge"],
      ["Settings", "/settings"],
    ]);
    expect(within(sheet).getByRole("link", { name: "Settings" })).toHaveAttribute("aria-current", "page");
    expect(screen.queryByTestId("mobile-menu-button")).not.toBeInTheDocument();
  });

  it.each(["/knowledge", "/lessons/lesson-42"])(
    "selects Knowledge on %s in the sidebar and marks More on the phone",
    (pathname) => {
      navigationState.pathname = pathname;
      render(
        <AppShell>
          <div>Page</div>
        </AppShell>,
      );
      const sidebar = screen.getByTestId("desktop-sidebar");
      expect(within(sidebar).getByRole("link", { name: "Knowledge" })).toHaveAttribute("aria-current", "page");
      expect(sidebar.querySelectorAll('[aria-current="page"]')).toHaveLength(1);
      const bottom = screen.getByTestId("mobile-bottom-navigation");
      expect(within(bottom).getByRole("button", { name: "More" })).toHaveAttribute("data-active", "true");
      expect(within(bottom).getByRole("link", { name: "Agent" })).toHaveAttribute("data-primary-workspace", "true");
      expect(screen.queryByTestId("secondary-navigation")).not.toBeInTheDocument();
    },
  );

  it("marks the Trades tab current on the phone bar", () => {
    navigationState.pathname = "/journal";
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    const bottom = screen.getByTestId("mobile-bottom-navigation");
    expect(within(bottom).getByRole("link", { name: "Trades" })).toHaveAttribute("aria-current", "page");
    expect(within(bottom).getByRole("button", { name: "More" })).not.toHaveAttribute("data-active");
  });

  it("opens the command menu from the phone status menu", () => {
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Status" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Search pages and destinations" }),
    );
    expect(screen.getByTestId("command-menu")).toBeInTheDocument();
    expect(
      screen.getByRole("option", { name: /Strategy Lab/i }),
    ).toBeInTheDocument();
  });

  it("omits a redundant Settings secondary tab on its parent page", () => {
    navigationState.pathname = "/settings";
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    expect(screen.queryByTestId("secondary-navigation")).not.toBeInTheDocument();
  });

  it("keeps diagnostic navigation contextual on operational routes", () => {
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    const secondary = screen.getByTestId("secondary-navigation");
    expect(within(secondary).getByRole("link", { name: "Back to Settings" })).toHaveAttribute("href", "/settings");
    expect(
      within(secondary).queryByRole("link", { name: "Advanced" }),
    ).not.toBeInTheDocument();
  });
  it("keeps missing execution posture explicit", () => {
    posture.executionMode = null;
    posture.realTradingEnabled = null;
    posture.postureKnown = false;
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    expect(
      screen.getByRole("status", { name: "Execution status" }),
    ).toHaveTextContent("Execution unverified");
  });
  it("hides desktop and mobile nav from opposite breakpoints via CSS classes", () => {
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    expect(screen.getByTestId("desktop-sidebar").className).toContain(
      "lg:flex",
    );
    expect(screen.getByTestId("desktop-sidebar").className).toContain("hidden");
    expect(screen.getByTestId("mobile-bottom-navigation").className).toContain(
      "lg:hidden",
    );
  });

  it("applies safe-area padding on mobile bottom navigation", () => {
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    expect(screen.getByTestId("mobile-bottom-navigation").className).toContain(
      "safe-area-inset-bottom",
    );
  });

  it("renders a skip link that targets and focuses #main (FP2-113)", () => {
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    const skipLink = screen.getByRole("link", { name: "Skip to main content" });
    expect(skipLink).toHaveAttribute("href", "#main");
    // Visually hidden until keyboard focus reveals it.
    expect(skipLink.className).toContain("sr-only");
    expect(skipLink.className).toContain("focus:not-sr-only");

    const main = document.getElementById("main");
    expect(main).not.toBeNull();
    expect(main).toHaveAttribute("tabindex", "-1");

    fireEvent.click(skipLink);
    expect(main).toHaveFocus();
  });

  it("gives every shell control a distinct accessible name (FP2-224)", () => {
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    const names = screen
      .getAllByRole("button")
      .map(
        (button) =>
          button.getAttribute("aria-label") ?? button.textContent?.trim() ?? "",
      )
      .filter((name) => /menu/i.test(name));
    expect(names.length).toBeGreaterThan(0);
    expect(new Set(names).size).toBe(names.length);
    expect(screen.getByRole("button", { name: "Status" })).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Search pages and destinations" }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Status" }));
    expect(
      screen.getByRole("button", { name: "Search pages and destinations" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Open navigation menu" }),
    ).not.toBeInTheDocument();
  });

  it("announces execution status politely", () => {
    render(
      <AppShell>
        <div>Page</div>
      </AppShell>,
    );
    expect(
      screen.getByRole("status", { name: "Execution status" }),
    ).toHaveAttribute("aria-live", "polite");
  });
});
