import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "@/components/layout/TopBar";
import { ShellFreshnessProvider } from "@/contexts/ShellFreshnessContext";

const posture = {
  executionMode: "paper" as string | null,
  realTradingEnabled: false as boolean | null,
  providerMode: "mock",
  postureKnown: true,
};

const navigationState = {
  pathname: "/",
};

type ProvidersState = { providers: { is_mock: boolean }[] } | null;

const appState: { providers: ProvidersState } = {
  providers: { providers: [] },
};

vi.mock("next/navigation", () => ({
  usePathname: () => navigationState.pathname,
}));

vi.mock("@/contexts/AppContext", () => ({
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
    health: {
      status: "ok",
      version: "0.1",
      execution_mode: "paper",
      real_trading_enabled: false,
    },
    providers: appState.providers,
  }),
  useSafetyPosture: () => posture,
}));

const logout = vi.fn();

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({
    user: { email: "trader@example.com" },
    organization: { name: "Alpha Org" },
    logout,
  }),
}));

vi.mock("@/components/KillSwitchButton", () => ({
  KillSwitchButton: () => <button type="button">Kill</button>,
}));

function renderTopBar(onOpenCommandMenu?: () => void) {
  return render(
    <ShellFreshnessProvider>
      <div className="w-[390px]">
        <TopBar onOpenCommandMenu={onOpenCommandMenu} />
      </div>
    </ShellFreshnessProvider>,
  );
}

describe("Compact truthful header", () => {
  beforeEach(() => {
    posture.executionMode = "paper";
    posture.realTradingEnabled = false;
    posture.postureKnown = true;
    navigationState.pathname = "/";
    logout.mockReset();
    appState.providers = { providers: [] };
  });
  afterEach(cleanup);
  it("has one mode label and describes only the global pause", () => {
    renderTopBar();
    expect(
      screen.getByRole("status", { name: "Execution status" }),
    ).toHaveTextContent("PAPER");
    expect(
      screen.getByRole("status", { name: "Execution status" }),
    ).toHaveTextContent("No global pause");
    expect(screen.queryByTestId("status-strip")).not.toBeInTheDocument();
    expect(screen.queryByText("Execution ready")).not.toBeInTheDocument();
    expect(screen.getByTestId("header-status-menu")).not.toHaveAttribute(
      "open",
    );
  });
  it("shows unknown when mode cannot be verified", () => {
    posture.executionMode = null;
    posture.realTradingEnabled = null;
    posture.postureKnown = false;
    renderTopBar();
    expect(
      screen.getByRole("status", { name: "Execution status" }),
    ).toHaveTextContent("Execution unverified");
  });
  it("does not show a verified paper label on conflicting real trading posture", () => {
    posture.realTradingEnabled = true;
    renderTopBar();
    expect(
      screen.getByRole("status", { name: "Execution status" }),
    ).not.toHaveTextContent(/^Paper/);
  });
  it("keeps secondary diagnostics and account controls in the dropdown", () => {
    appState.providers = null;
    renderTopBar();
    fireEvent.click(screen.getByText("Status", { selector: "summary" }));
    expect(screen.getByTestId("header-status-menu")).toHaveAttribute("open");
    expect(screen.getByText("Providers unknown")).toBeVisible();
    expect(
      screen.getByText("Strategy and account gates still apply."),
    ).toBeVisible();
    expect(screen.getByText("trader@example.com")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));
    expect(logout).toHaveBeenCalledOnce();
  });
  it("closes on Escape and restores keyboard focus", () => {
    renderTopBar();
    const summary = screen.getByText("Status", { selector: "summary" });
    fireEvent.click(summary);
    fireEvent.keyDown(screen.getByRole("button", { name: "Log out" }), {
      key: "Escape",
    });
    expect(screen.getByTestId("header-status-menu")).not.toHaveAttribute(
      "open",
    );
    expect(summary).toHaveFocus();
  });
  it("opens search from the secondary menu and dismisses it", () => {
    const openSearch = vi.fn();
    renderTopBar(openSearch);
    expect(screen.getByRole("button", { name: "Search pages and destinations" })).not.toBeVisible();
    fireEvent.click(screen.getByText("Status", { selector: "summary" }));
    fireEvent.click(screen.getByRole("button", { name: "Search pages and destinations" }));
    expect(openSearch).toHaveBeenCalledOnce();
    expect(screen.getByTestId("header-status-menu")).not.toHaveAttribute("open");
  });
  it("dismisses the secondary menu on an outside pointer press", () => {
    renderTopBar();
    fireEvent.click(screen.getByText("Status", { selector: "summary" }));
    fireEvent.pointerDown(document.body);
    expect(screen.getByTestId("header-status-menu")).not.toHaveAttribute("open");
  });
  it("keeps the global identity separate from the page heading", () => {
    navigationState.pathname = "/knowledge";
    renderTopBar();
    expect(screen.getByTestId("topbar-page-identity")).toHaveTextContent(
      "AlphaTrade",
    );
    expect(screen.queryByText("Journal & Knowledge")).not.toBeInTheDocument();
  });
});
