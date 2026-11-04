import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import nextConfig from "../../../../next.config";
import AutonomousPage from "./page";
import RiskRedirectPage from "../risk/page";
import BotRedirectPage from "../risk/autonomous/page";

const appContext = {
  killSwitchStatus: null as { active?: boolean; execution_blocked: boolean; reason: string | null } | null,
  killSwitchError: null as string | null,
  killSwitchActive: false,
  killSwitchBusy: false,
  setKillSwitchActive: vi.fn(),
  loading: false,
};
vi.mock("@/contexts/AppContext", () => ({
  useAppContext: () => appContext,
  useSafetyPosture: () => ({ executionMode: "paper", realTradingEnabled: false, postureKnown: true }),
}));
vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user" }, organization: { id: "org" }, membershipRole: "owner" }),
}));
vi.mock("@/contexts/ShellFreshnessContext", () => ({
  useShellFreshness: () => ({ freshness: { state: null }, setFreshness: vi.fn(), clearFreshness: vi.fn() }),
}));
vi.mock("@/components/autonomous/BotSettings", () => ({
  BotSettings: () => (
    <>
      <section aria-label="Paper bot" />
      <section id="limits" aria-label="Trading limits" />
      <section id="guardrails" aria-label="Stop-loss band and hard ceilings" />
      <section id="grade-risk" aria-label="Grade risk" />
      <section id="leverage" aria-label="Leverage" />
      <section aria-label="What the bot is doing" />
    </>
  ),
}));
vi.mock("@/components/autonomous/StrategyStatsPanel", () => ({
  StrategyStatsPanel: () => <section id="strategies" aria-label="Per strategy" />,
}));

beforeEach(() => {
  appContext.killSwitchStatus = { active: false, execution_blocked: false, reason: null };
});
afterEach(cleanup);

describe("Autonomous page", () => {
  it("puts the bot, all limits, grade risk, leverage and per-strategy stats on one page", () => {
    render(<AutonomousPage />);
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Autonomous Trading"]);
    const header = screen.getByRole("heading", { level: 1 }).closest("header")!;
    expect(within(header).getByRole("button", { name: /pause|kill|activate/i })).toBeInTheDocument();
    expect(screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"))).toEqual([
      "Paper bot",
      "Trading limits",
      "Stop-loss band and hard ceilings",
      "Grade risk",
      "Leverage",
      "What the bot is doing",
      "Per strategy",
    ]);
    const nav = screen.getByRole("navigation", { name: "Autonomous Trading sections" });
    for (const link of within(nav).getAllByRole("link")) {
      expect(document.querySelector(link.getAttribute("href")!)).not.toBeNull();
    }
    expect(screen.getByTestId("paper-mode-indicator")).toBeInTheDocument();
    expect(screen.getByTestId("autonomous-settings-ownership")).toHaveTextContent(
      "Real-money execution stays blocked.",
    );
    expect(screen.getByTestId("autonomous-real-trading-footer")).toHaveTextContent("Real trading remains disabled.");
  });

  it("shows the kill switch status read-only", () => {
    render(<AutonomousPage />);
    const card = screen.getByTestId("kill-switch-status");
    expect(card).toHaveAttribute("id", "kill-switch");
    expect(card).toHaveTextContent("Kill switchOff");
    expect(card).toHaveTextContent("ExecutionAllowed");
    expect(within(card).queryByRole("button")).not.toBeInTheDocument();
  });

  it("shows the BLOCK panel while the kill switch blocks execution", () => {
    appContext.killSwitchStatus = { active: true, execution_blocked: true, reason: "Drawdown breach" };
    render(<AutonomousPage />);
    expect(screen.getByTestId("kill-switch-status")).toHaveTextContent("Reason: Drawdown breach");
    expect(screen.getByTestId("portfolio-risk-block")).toHaveTextContent(
      "Kill switch blocking execution: Drawdown breach",
    );
  });

  it("renders the same view at the old /risk pages", () => {
    render(<RiskRedirectPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Autonomous Trading" })).toBeInTheDocument();
    cleanup();
    render(<BotRedirectPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Autonomous Trading" })).toBeInTheDocument();
  });

  it.each([
    ["/risk", "/autonomous#limits"],
    ["/risk/autonomous", "/autonomous#limits"],
    ["/watchlist", "/settings/watcher"],
  ])("redirects %s to %s", async (source, destination) => {
    const rules = await nextConfig.redirects!();
    expect(rules).toContainEqual({ source, destination, permanent: false });
  });
});
