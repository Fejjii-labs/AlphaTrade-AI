import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KillSwitchButton } from "@/components/KillSwitchButton";

const setKillSwitchActive = vi.fn();
const refreshKillSwitch = vi.fn();

const appState = {
  killSwitchActive: false,
  killSwitchBusy: false,
  killSwitchError: null as string | null,
  setKillSwitchActive,
  refreshKillSwitch,
};

vi.mock("@/contexts/AppContext", () => ({
  useAppContext: () => appState,
}));

/**
 * FP2-203 — the kill switch is a destructive server-side control, so its
 * confirmation must be an in-app accessible dialog rather than browser
 * `confirm`/`prompt`.
 */
describe("KillSwitchButton confirmation (FP2-203)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    appState.killSwitchActive = false;
    appState.killSwitchBusy = false;
    appState.killSwitchError = null;
    setKillSwitchActive.mockResolvedValue(undefined);
  });

  afterEach(() => cleanup());

  function openConfirm() {
    render(<KillSwitchButton />);
    fireEvent.click(screen.getByTestId("kill-switch-button"));
    return screen.getByRole("dialog");
  }

  it("does not use browser confirm or prompt", () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("reason");

    openConfirm();

    expect(confirmSpy).not.toHaveBeenCalled();
    expect(promptSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
    promptSpy.mockRestore();
  });

  it("opens an accessible modal dialog naming the pending action", () => {
    const dialog = openConfirm();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(
      screen.getByRole("heading", { name: "Activate organization kill switch?" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("kill-switch-button")).toHaveAttribute("aria-expanded", "true");
  });

  it("requires a reason of at least three characters before submitting", async () => {
    openConfirm();
    fireEvent.change(screen.getByTestId("kill-switch-reason"), { target: { value: "no" } });
    fireEvent.click(screen.getByTestId("kill-switch-confirm-action"));

    await waitFor(() =>
      expect(
        screen.getByText("A reason of at least 3 characters is required."),
      ).toBeInTheDocument(),
    );
    expect(setKillSwitchActive).not.toHaveBeenCalled();
    expect(screen.getByTestId("kill-switch-reason")).toHaveAttribute("aria-invalid", "true");
  });

  it("submits the trimmed reason and closes on success", async () => {
    openConfirm();
    fireEvent.change(screen.getByTestId("kill-switch-reason"), {
      target: { value: "  Emergency halt  " },
    });
    fireEvent.click(screen.getByTestId("kill-switch-confirm-action"));

    await waitFor(() =>
      expect(setKillSwitchActive).toHaveBeenCalledWith(true, "Emergency halt"),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("deactivates when the switch is already active", async () => {
    appState.killSwitchActive = true;
    openConfirm();
    expect(
      screen.getByRole("heading", { name: "Deactivate organization kill switch?" }),
    ).toBeInTheDocument();

    fireEvent.change(screen.getByTestId("kill-switch-reason"), {
      target: { value: "Resume paper trading" },
    });
    fireEvent.click(screen.getByTestId("kill-switch-confirm-action"));

    await waitFor(() =>
      expect(setKillSwitchActive).toHaveBeenCalledWith(false, "Resume paper trading"),
    );
  });

  it("keeps the dialog open and surfaces the failure when the update is rejected", async () => {
    setKillSwitchActive.mockRejectedValueOnce(new Error("forbidden"));
    openConfirm();
    fireEvent.change(screen.getByTestId("kill-switch-reason"), { target: { value: "halt" } });
    fireEvent.click(screen.getByTestId("kill-switch-confirm-action"));

    await waitFor(() =>
      expect(
        screen.getByText("Couldn't update. Try again."),
      ).toBeInTheDocument(),
    );
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("shows a short error with Retry under the button", () => {
    appState.killSwitchError = "Couldn't load.";
    render(<KillSwitchButton />);
    expect(screen.getByRole("alert")).toHaveTextContent("Couldn't load.");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(refreshKillSwitch).toHaveBeenCalledOnce();
  });

  it("cancels without calling the API and restores focus to the trigger", () => {
    openConfirm();
    fireEvent.click(screen.getByTestId("kill-switch-cancel"));

    expect(setKillSwitchActive).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByTestId("kill-switch-button")).toHaveFocus();
  });

  it("closes on Escape", () => {
    openConfirm();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("submits from the reason field with Enter", async () => {
    openConfirm();
    const reason = screen.getByTestId("kill-switch-reason");
    fireEvent.change(reason, { target: { value: "halt now" } });
    fireEvent.keyDown(reason, { key: "Enter" });

    await waitFor(() => expect(setKillSwitchActive).toHaveBeenCalledWith(true, "halt now"));
  });
});
