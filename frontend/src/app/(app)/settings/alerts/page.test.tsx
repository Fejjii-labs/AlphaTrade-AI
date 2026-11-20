import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AlertsSettingsPage from "./page";

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: "user-1" }, organization: { id: "org-1" } }),
}));
vi.mock("@/hooks/useWatcherMonitoring", () => ({
  useWatcherMonitoring: () => ({ loading: false, data: null }),
}));
vi.mock("@/components/NotificationSettingsPanel", () => ({
  NotificationSettingsPanel: () => <section data-testid="notification-settings" />,
}));
vi.mock("@/components/dashboard/AttentionCard", () => ({
  AttentionCard: ({ limit }: { limit?: number }) => (
    <section data-testid="dashboard-attention" data-limit={String(limit)} />
  ),
}));

afterEach(cleanup);

describe("Settings > Alerts", () => {
  it("shows the full alert queue and the Telegram settings", () => {
    render(<AlertsSettingsPage />);
    expect(screen.getByRole("heading", { level: 1, name: "Alerts" })).toBeInTheDocument();
    expect(screen.getByTestId("dashboard-attention")).toHaveAttribute("data-limit", "Infinity");
    expect(screen.getByTestId("notification-settings")).toBeInTheDocument();
  });
});
