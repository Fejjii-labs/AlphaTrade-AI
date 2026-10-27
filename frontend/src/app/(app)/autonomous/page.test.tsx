import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import nextConfig from "../../../../next.config";
import AutonomousPage from "./page";
import RiskRedirectPage from "../risk/page";
import BotRedirectPage from "../risk/autonomous/page";

const appContext = {
  killSwitchStatus: null as { execution_blocked: boolean; reason: string | null } | null,
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
  BotSettings: ({ between }: { between: React.ReactNode }) => (
    <>
      <section aria-label="Paper bot" />
      {between}
      <section id="bot" aria-label="Bot limits" />
    </>
  ),
}));
vi.mock("@/components/autonomous/AccountLimitsCard", () => ({
  AccountLimitsCard: () => <section id="account" aria-label="Account limits" />,
}));

beforeEach(() => {
  appContext.killSwitchStatus = { execution_blocked: false, reason: null };
});
afterEach(cleanup);

describe("Autonomous page", () => {
  it("puts the bot, its limits and the account limits on one page with the kill switch", () => {
    render(<AutonomousPage />);
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Autonomous"]);
    const header = screen.getByRole("heading", { level: 1 }).closest("header")!;
    expect(within(header).getByRole("button", { name: /pause|kill|activate/i })).toBeInTheDocument();
    expect(screen.getAllByRole("region").map((region) => region.getAttribute("aria-label"))).toEqual([
      "Paper bot",
      "Account limits",
      "Bot limits",
    ]);
    expect(screen.getByRole("region", { name: "Bot limits" })).toHaveAttribute("id", "bot");
    expect(screen.getByRole("region", { name: "Account limits" })).toHaveAttribute("id", "account");
    expect(screen.getByTestId("paper-mode-indicator")).toBeInTheDocument();
  });

  it("shows the BLOCK panel while the kill switch blocks execution", () => {
    appContext.killSwitchStatus = { execution_blocked: true, reason: "Drawdown breach" };
    render(<AutonomousPage />);
    expect(screen.getByTestId("portfolio-risk-block")).toHaveTextContent(
      "Kill switch blocking execution: Drawdown breach",
    );
  });

  it("renders the same view at the old /risk pages", () => {
    render(<RiskRedirectPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Autonomous" })).toBeInTheDocument();
    cleanup();
    render(<BotRedirectPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Autonomous" })).toBeInTheDocument();
  });

  it.each([
    ["/risk", "/autonomous#account"],
    ["/risk/autonomous", "/autonomous#bot"],
    ["/watchlist", "/settings/watcher"],
  ])("redirects %s to %s", async (source, destination) => {
    const rules = await nextConfig.redirects!();
    expect(rules).toContainEqual({ source, destination, permanent: false });
  });
});
