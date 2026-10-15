import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { BrainSetup } from "@/lib/api/brain-types";
import BrainSetupPage from "./page";

const mocks = vi.hoisted(() => ({ setup: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: { strategyBrain: mocks } }));
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "sfp-setup" }),
  useSearchParams: () =>
    new URLSearchParams("returnTo=%2Fstrategies%2Fdetections%3Ffamily%3Dsfp"),
}));
const setup: BrainSetup = {
  setup_id: "sfp-setup",
  strategy_version_id: "sfp-version",
  family: "sfp",
  timeframe: "4h",
  instrument: "BTCUSDT",
  direction: "long",
  condition: "confirmed_sfp",
  state: "CONFIRMED",
  observed_at: "2026-10-01T00:00:00Z",
  expires_at: "2026-10-10T00:00:00Z",
  fresh_until: "2026-10-10T00:00:00Z",
  freshness: "AVAILABLE",
  risk_state: "not_evaluated",
  reason_codes: [],
  evidence_reference: "hash",
  evidence: {},
  candidate_id: null,
  decision_id: null,
  journal: null,
  quality_components: {},
  entry: null,
  stop: null,
  targets: [],
};
// The page expires stored setups against the wall clock (useBrainSetupClock).
// Pin "now" inside the fixture's observed→expires window so the stored state is
// rendered deterministically; only Date is faked so async UI timers still run.
const INSIDE_WINDOW = new Date("2026-10-05T00:00:00Z");
beforeEach(() => {
  vi.useFakeTimers({ toFake: ["Date"], now: INSIDE_WINDOW });
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  vi.useRealTimers();
});
it("shows the SFP condition, bound timeframe and research scope in stored details", async () => {
  mocks.setup.mockResolvedValue(setup);
  render(<BrainSetupPage />);
  expect(
    await screen.findByRole("heading", {
      name: "SFP confirmed_sfp · CONFIRMED",
    }),
  ).toBeInTheDocument();
  expect(
    screen.getByText(/BTCUSDT · long · 4h · evidence/),
  ).toBeInTheDocument();
  expect(screen.getByText("Strategy version sfp-version")).toBeInTheDocument();
  expect(
    screen.getByText(/No SFP execution plan is authorized/),
  ).toBeInTheDocument();
  expect(screen.queryByText(/Entry concept/)).toBeNull();
  expect(
    screen.getByRole("link", { name: "← Back to detections" }),
  ).toHaveAttribute("href", "/strategies/detections?family=sfp");
});

it("shows a stored CONFIRMED setup as EXPIRED once its expiry has passed", async () => {
  vi.setSystemTime(new Date("2026-10-10T00:00:01Z"));
  mocks.setup.mockResolvedValue(setup);
  render(<BrainSetupPage />);
  expect(
    await screen.findByRole("heading", { name: "SFP confirmed_sfp · EXPIRED" }),
  ).toBeInTheDocument();
});
