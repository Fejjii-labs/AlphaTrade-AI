import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useEffect } from "react";

import {
  ShellFreshnessProvider,
  useShellFreshness,
} from "@/contexts/ShellFreshnessContext";
import { WorkflowFreshnessAdapter } from "@/components/workflows/WorkflowFreshnessAdapter";
import { FreshnessPill } from "@/components/ui/freshness-pill";
import { TopBar } from "@/components/layout/TopBar";
import type { FreshnessSourceInput } from "@/components/workflows/freshness";

afterEach(cleanup);

function liveTimestamp(): string {
  return new Date(Date.now() - 60_000).toISOString();
}

vi.mock("next/navigation", () => ({
  usePathname: () => "/",
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
    providers: { providers: [] },
  }),
  useSafetyPosture: () => ({
    executionMode: "paper",
    realTradingEnabled: false,
    providerMode: "mock",
    postureKnown: true,
  }),
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

function FreshnessProbe() {
  const { freshness, setFreshness, clearFreshness } = useShellFreshness();
  return (
    <div>
      <span data-testid="probe-state">{freshness.state ?? "null"}</span>
      <span data-testid="probe-age">{freshness.ageLabel ?? ""}</span>
      {freshness.state ? (
        <FreshnessPill state={freshness.state} ageLabel={freshness.ageLabel} />
      ) : (
        <span>Freshness unavailable</span>
      )}
      <button
        type="button"
        onClick={() => setFreshness({ state: "live", ageLabel: "1m" })}
      >
        Set live
      </button>
      <button type="button" onClick={() => clearFreshness()}>
        Clear
      </button>
    </div>
  );
}

describe("ShellFreshnessContext", () => {
  it("defaults to unavailable and supports set/clear", () => {
    render(
      <ShellFreshnessProvider>
        <FreshnessProbe />
      </ShellFreshnessProvider>,
    );
    expect(screen.getByTestId("probe-state")).toHaveTextContent("null");
    expect(screen.getByText("Freshness unavailable")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Set live" }));
    expect(screen.getByTestId("probe-state")).toHaveTextContent("live");
    expect(screen.getByTestId("freshness-pill")).toHaveTextContent("Live");

    fireEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(screen.getByTestId("probe-state")).toHaveTextContent("null");
  });

  it("wires honest timestamps through WorkflowFreshnessAdapter only", () => {
    render(
      <ShellFreshnessProvider>
        <WorkflowFreshnessAdapter
          sources={[
            {
              name: "a",
              available: true,
              required: true,
              timestamp: liveTimestamp(),
            },
          ]}
          clearOnUnmount={false}
        />
        <FreshnessProbe />
      </ShellFreshnessProvider>,
    );
    expect(screen.getByTestId("probe-state").textContent).not.toBe("null");
  });

  it("keeps freshness unavailable when no timestamp exists", () => {
    render(
      <ShellFreshnessProvider>
        <WorkflowFreshnessAdapter timestamps={[null, undefined]} clearOnUnmount={false} />
        <FreshnessProbe />
      </ShellFreshnessProvider>,
    );
    expect(screen.getByTestId("probe-state")).toHaveTextContent("unavailable");
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
  });

  it("does not mark the page live when a required source failed", () => {
    render(
      <ShellFreshnessProvider>
        <WorkflowFreshnessAdapter
          sources={[
            {
              name: "live",
              available: true,
              required: true,
              timestamp: liveTimestamp(),
            },
            {
              name: "failed",
              available: false,
              required: true,
              timestamp: null,
            },
          ]}
          clearOnUnmount={false}
        />
        <FreshnessProbe />
      </ShellFreshnessProvider>,
    );
    expect(screen.getByTestId("probe-state")).toHaveTextContent("unavailable");
  });

  it("does not mark the page live when one source has unknown freshness", () => {
    render(
      <ShellFreshnessProvider>
        <WorkflowFreshnessAdapter
          sources={[
            {
              name: "live",
              available: true,
              required: true,
              timestamp: liveTimestamp(),
            },
            {
              name: "unknown",
              available: true,
              required: true,
              timestamp: null,
            },
          ]}
          clearOnUnmount={false}
        />
        <FreshnessProbe />
      </ShellFreshnessProvider>,
    );
    expect(screen.getByTestId("probe-state")).toHaveTextContent("unavailable");
    expect(screen.queryByText("Live")).not.toBeInTheDocument();
  });

  it("TopBar does not display Live when one relevant page source has unknown freshness", () => {
    render(
      <ShellFreshnessProvider>
        <WorkflowFreshnessAdapter
          sources={[
            {
              name: "live",
              available: true,
              required: true,
              timestamp: liveTimestamp(),
            },
            {
              name: "unknown",
              available: true,
              required: true,
              timestamp: null,
            },
          ]}
          clearOnUnmount={false}
        />
        <TopBar />
      </ShellFreshnessProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Status" }));
    const freshnessRegion = screen.getByTestId("topbar-freshness");
    expect(within(freshnessRegion).queryByText(/^Live/)).not.toBeInTheDocument();
    expect(
      within(freshnessRegion).getByText(/Unavailable|Freshness unavailable/i),
    ).toBeInTheDocument();
  });

  it("does not re-push context for an equivalently recreated sources array", () => {
    const freshnessUpdates: Array<string | null> = [];
    const timestamp = liveTimestamp();

    function CountingProbe() {
      const { freshness } = useShellFreshness();
      useEffect(() => {
        freshnessUpdates.push(freshness.state);
      }, [freshness]);
      return <span data-testid="probe-state">{freshness.state ?? "null"}</span>;
    }

    function Harness({ sources }: { sources: FreshnessSourceInput[] }) {
      return (
        <ShellFreshnessProvider>
          <WorkflowFreshnessAdapter sources={sources} clearOnUnmount={false} />
          <CountingProbe />
        </ShellFreshnessProvider>
      );
    }

    const { rerender } = render(
      <Harness
        sources={[
          {
            name: "a",
            available: true,
            required: true,
            timestamp,
          },
        ]}
      />,
    );

    expect(screen.getByTestId("probe-state")).toHaveTextContent("live");
    const updatesAfterFirst = freshnessUpdates.length;

    rerender(
      <Harness
        sources={[
          {
            name: "a",
            available: true,
            required: true,
            timestamp,
          },
        ]}
      />,
    );

    expect(freshnessUpdates.length).toBe(updatesAfterFirst);
    expect(screen.getByTestId("probe-state")).toHaveTextContent("live");
  });
});
