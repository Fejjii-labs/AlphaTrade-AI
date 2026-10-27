import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { TelegramPolicyForm } from "./TelegramPolicyForm";
import { telegramPolicyFixture as policy } from "./telegram-policy.fixture";

const NESTED = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"];
const options = {
  symbols: [
    { label: "BTCUSDT", values: ["BTCUSDT"] },
    { label: "ETHUSDT", values: ["ETHUSDT"] },
  ],
  strategies: [{ label: "Nested", values: NESTED }],
  setup_stages: ["N1", "N2", "N3"].map((v) => ({ label: v, values: [v] })),
};
const UUID = /[0-9a-f]{8}-[0-9a-f]{4}/i;

beforeEach(() => {
  vi.spyOn(api.notifications, "updatePreferences").mockResolvedValue({
    telegram_policy: policy,
    telegram_enabled: true,
    in_app_enabled: true,
    webhook_enabled: false,
    min_severity: "info",
  });
  vi.spyOn(api.notifications, "telegramOptions").mockResolvedValue(options);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
function mount(savedPolicy = policy) {
  const onSaved = vi.fn().mockResolvedValue(undefined);
  render(
    <TelegramPolicyForm policy={savedPolicy} enabled={true} onSaved={onSaved} />,
  );
  return onSaved;
}
function save() {
  fireEvent.click(screen.getByRole("button", { name: "Save Telegram policy" }));
}

describe("Telegram policy V2 full replacement", () => {
  it("preserves SFP lifecycle allowlists while editing shared phase preferences", async () => {
    const savedPolicy = {
      ...policy,
      setup_stages: null,
      event_types: ["SFP_CONFIRMED", "SFP_BLOCKED_BY_RISK"],
    } satisfies typeof policy;
    mount(savedPolicy);
    fireEvent.click(screen.getByLabelText("Forming setup notifications"));
    save();
    await waitFor(() =>
      expect(api.notifications.updatePreferences).toHaveBeenCalledExactlyOnceWith({
        telegram_enabled: true,
        telegram_policy: { ...savedPolicy, forming_alerts: true },
      }),
    );
  });
  it("renders saved filters as pressed chips and submits edits with other filters intact", async () => {
    const onSaved = mount();
    const symbols = screen.getByRole("group", { name: "Watched symbols choices" });
    expect(await within(symbols).findByRole("button", { name: "BTCUSDT" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    const stages = screen.getByRole("group", { name: "Nested stage filters choices" });
    expect(within(stages).getByRole("button", { name: "N2" })).toHaveAttribute("aria-pressed", "true");
    expect(within(stages).getByRole("button", { name: "N1" })).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByLabelText("Minimum Telegram severity")).toHaveValue("WATCH");
    expect(screen.getByLabelText("Minimum quality threshold")).toHaveValue(null);
    fireEvent.click(within(symbols).getByRole("button", { name: "ETHUSDT" }));
    fireEvent.change(screen.getByLabelText("Cooldown (seconds)"), {
      target: { value: "300" },
    });
    fireEvent.click(screen.getByLabelText("Forming setup notifications"));
    save();
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(api.notifications.updatePreferences).toHaveBeenCalledExactlyOnceWith({
      telegram_enabled: true,
      telegram_policy: {
        ...policy,
        symbol_subscriptions: ["BTCUSDT", "ETHUSDT"],
        cooldown_seconds: 300,
        forming_alerts: true,
      },
    });
  });
  it("maps a strategy chip to all of its stored identifiers and never shows them", async () => {
    const stray = "33333333-3333-4333-8333-333333333333";
    const { container } = render(
      <TelegramPolicyForm
        policy={{ ...policy, strategy_subscriptions: [stray] }}
        enabled={true}
        onSaved={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    const strategies = screen.getByRole("group", { name: "Watched strategies choices" });
    fireEvent.click(await within(strategies).findByRole("button", { name: "Nested" }));
    expect(within(strategies).getByRole("button", { name: "Nested" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(strategies).getByText("Other saved (1) ✕")).toBeInTheDocument();
    expect(container.textContent).not.toMatch(UUID);
    fireEvent.click(
      within(strategies).getByRole("button", { name: "Remove 1 other saved watched strategies" }),
    );
    save();
    await waitFor(() =>
      expect(api.notifications.updatePreferences).toHaveBeenCalledExactlyOnceWith({
        telegram_enabled: true,
        telegram_policy: { ...policy, strategy_subscriptions: NESTED },
      }),
    );
  });
  it("keeps saved picks when the choices cannot be loaded", async () => {
    vi.mocked(api.notifications.telegramOptions).mockRejectedValueOnce(new Error("down"));
    const onSaved = mount();
    expect(
      await within(screen.getByRole("group", { name: "Watched symbols choices" })).findByText(
        "Choices unavailable. Saved picks are kept.",
      ),
    ).toBeInTheDocument();
    save();
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
    expect(api.notifications.updatePreferences).toHaveBeenCalledExactlyOnceWith({
      telegram_enabled: true,
      telegram_policy: policy,
    });
  });
  it("has no free-text identifier inputs", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("Watched strategies"), {
      target: { value: "selected" },
    });
    await screen.findByRole("button", { name: "Nested" });
    expect(screen.queryAllByLabelText(/identifiers/i)).toHaveLength(0);
    // The only free-text box left is the quiet-hours timezone.
    expect(screen.getAllByRole("textbox").map((el) => el.getAttribute("value"))).toEqual([
      "Europe/Berlin",
    ]);
  });
  it("preserves all versus none subscriptions and explicit zero quality", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("Watched symbols"), {
      target: { value: "none" },
    });
    fireEvent.change(screen.getByLabelText("Nested stage filters"), {
      target: { value: "all" },
    });
    fireEvent.change(screen.getByLabelText("Minimum quality threshold"), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByLabelText("Enable Telegram quiet hours"));
    fireEvent.click(
      screen.getByLabelText("Enable Telegram notification policy"),
    );
    save();
    await waitFor(() =>
      expect(api.notifications.updatePreferences).toHaveBeenCalledWith({
        telegram_enabled: false,
        telegram_policy: {
          ...policy,
          symbol_subscriptions: [],
          setup_stages: null,
          minimum_quality: 0,
          quiet_hours: null,
        },
      }),
    );
  });
  it("validates quiet hour boundaries before saving", async () => {
    mount();
    fireEvent.change(screen.getByLabelText("Quiet hours end"), {
      target: { value: "22:00" },
    });
    save();
    await screen.findByText("Quiet hours start and end must differ.");
    fireEvent.change(screen.getByLabelText("Quiet hours end"), {
      target: { value: "07:00" },
    });
    fireEvent.change(screen.getByLabelText("Quiet hours timezone"), {
      target: { value: "Unknown/Timezone" },
    });
    save();
    await screen.findByText("Use a valid IANA timezone for quiet hours.");
    expect(api.notifications.updatePreferences).not.toHaveBeenCalled();
  });
  it("retains the draft for retry on API failure without reporting saved", async () => {
    const onSaved = mount();
    vi.mocked(api.notifications.updatePreferences).mockRejectedValueOnce(
      new Error("private backend detail"),
    );
    fireEvent.change(screen.getByLabelText("Minimum Telegram severity"), {
      target: { value: "ACTION" },
    });
    save();
    await screen.findByText(
      "Telegram policy could not be saved. Please try again.",
    );
    expect(screen.getByLabelText("Minimum Telegram severity")).toHaveValue(
      "ACTION",
    );
    expect(onSaved).not.toHaveBeenCalled();
    save();
    await waitFor(() => expect(onSaved).toHaveBeenCalledOnce());
  });
});
