import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RealtimeVoiceControls } from "./RealtimeVoiceControls";
import type { RealtimeSnapshot, RealtimeVoiceController } from "@/lib/voice/realtime-controller";

type Options = ConstructorParameters<typeof RealtimeVoiceController>[0];
const mocks = vi.hoisted(() => ({ instances: [] as Array<{
  options: Options; start: ReturnType<typeof vi.fn>; end: ReturnType<typeof vi.fn>;
  interrupt: ReturnType<typeof vi.fn>; resume: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>; setConversation: ReturnType<typeof vi.fn>;
}> }));

const config = vi.hoisted(() => ({ voiceConfig: vi.fn() }));
// Availability is read on mount; by default it stays unknown (pending), as before B7b.
vi.mock("@/lib/api", () => ({ api: { agent: config } }));

vi.mock("@/lib/voice/realtime-controller", () => ({
  RealtimeVoiceController: class {
    start = vi.fn(); end = vi.fn(); interrupt = vi.fn(); resume = vi.fn();
    dispose = vi.fn(); setConversation = vi.fn();
    meter = vi.fn(() => ({ speaking: false, level: 0 }));
    constructor(public options: Options) { mocks.instances.push(this); }
  },
}));

function props() {
  return {
    authenticated: true, sessionKey: "session", contextKey: "context", conversationId: "conversation",
    disabled: false, ensureConversation: vi.fn().mockResolvedValue("conversation"),
    onPending: vi.fn().mockReturnValue(true), onResult: vi.fn().mockReturnValue(true), onActive: vi.fn(),
  };
}
const active: RealtimeSnapshot = { state: "listening", active: true, error: null, transcript: "", provider: "gpt_live" };
const snapshot = (state: RealtimeSnapshot) => act(() => mocks.instances.at(-1)!.options.changed(state));
beforeEach(() => { config.voiceConfig.mockReset().mockReturnValue(new Promise(() => {})); });
afterEach(() => { cleanup(); mocks.instances.length = 0; vi.restoreAllMocks(); });

describe("GPT Live controls (mocked controller, no microphone)", () => {
  it("preserves disabled and unauthenticated start gating", () => {
    const p = props(); const view = render(<RealtimeVoiceControls {...p} disabled />);
    expect(screen.getByRole("button", { name: "Talk to Agent" })).toBeDisabled();
    view.rerender(<RealtimeVoiceControls {...p} authenticated={false} />);
    expect(screen.getByRole("button", { name: "Talk to Agent" })).toBeDisabled();
    expect(mocks.instances[0].start).not.toHaveBeenCalled(); expect(mocks.instances[0].end).toHaveBeenCalled();
  });

  it("labels unavailable GPT Live without starting a microphone or altering fallback controls", () => {
    render(<RealtimeVoiceControls {...props()} />);
    snapshot({ ...active, state: "error", active: false, error: "GPT Live is unavailable. Continue typing or use Record." });
    expect(screen.getByRole("alert")).toHaveTextContent("GPT Live is unavailable");
    expect(screen.queryByRole("button", { name: "Interrupt" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Resume audio" })).not.toBeInTheDocument();
    expect(mocks.instances[0].start).not.toHaveBeenCalled();
  });

  it("one Talk to Agent button opens the hands-free orb with resume and end controls", () => {
    const p = props(); render(<RealtimeVoiceControls {...p} />); const voice = mocks.instances[0];
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Talk to Agent" })); expect(voice.start).toHaveBeenCalledOnce();
    snapshot(active);
    const dialog = screen.getByRole("dialog", { name: "Voice conversation" });
    expect(screen.getByRole("status")).toHaveTextContent("Listening");
    expect(dialog).toHaveTextContent("GPT Live");
    // Hands-free: no per-reply press and no manual interrupt; barge-in is by voice.
    expect(screen.queryByRole("button", { name: "Interrupt" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Talk to Agent" })).toBeDisabled();
    snapshot({ ...active, outputMuted: true });
    expect(dialog).toHaveTextContent("Audio muted");
    fireEvent.click(screen.getByRole("button", { name: "Resume audio" })); expect(voice.resume).toHaveBeenCalledOnce();
    snapshot({ ...active, outputMuted: false });
    expect(screen.queryByRole("button", { name: "Resume audio" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "End" })); expect(voice.end).toHaveBeenCalledOnce();
    fireEvent.keyDown(document, { key: "Escape" }); expect(voice.end).toHaveBeenCalledTimes(2);
    expect(p.onActive).toHaveBeenCalledWith(true);
    snapshot({ ...active, active: false, state: "idle" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders captions separately from the heard Agent request", () => {
    const p = props(); render(<RealtimeVoiceControls {...p} />);
    snapshot({ ...active, transcript: "Saved heard request", inputCaption: "Live input fragment", replyCaption: "Live output fragment" });
    expect(screen.getByText("Heard request")).toBeInTheDocument();
    expect(screen.getByLabelText("Your speech caption")).toHaveTextContent("Live input fragment");
    expect(screen.getByLabelText("Agent speech caption")).toHaveTextContent("Live output fragment");
    expect(p.onPending).not.toHaveBeenCalled(); expect(p.onResult).not.toHaveBeenCalled();
  });

  it("ends on hidden navigation, context replacement and unmount", () => {
    const p = props(); const view = render(<RealtimeVoiceControls {...p} />); const first = mocks.instances[0];
    vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    fireEvent(document, new Event("visibilitychange")); expect(first.end).toHaveBeenCalledOnce();
    view.rerender(<RealtimeVoiceControls {...p} contextKey="another-context" />);
    expect(first.dispose).toHaveBeenCalledOnce();
    const second = mocks.instances[1];
    view.unmount(); expect(second.dispose).toHaveBeenCalledOnce(); expect(p.onActive).toHaveBeenLastCalledWith(false);
  });

  it("keeps conversation and logout cleanup attached to the current controller", () => {
    const p = props(); const view = render(<RealtimeVoiceControls {...p} />); const voice = mocks.instances[0];
    view.rerender(<RealtimeVoiceControls {...p} conversationId="different-conversation" />);
    expect(voice.setConversation).toHaveBeenLastCalledWith("different-conversation");
    view.rerender(<RealtimeVoiceControls {...p} authenticated={false} />);
    expect(voice.end).toHaveBeenCalledOnce();
  });
});

describe("voice availability, countdown and limits (UI overhaul B7b)", () => {
  it("hides the live mic when voice is off and reports it so dictation stays the one mic", async () => {
    config.voiceConfig.mockResolvedValue({ available: false, reason: "voice_not_enabled", provider: "gpt_live" });
    const p = { ...props(), onAvailability: vi.fn() };
    render(<RealtimeVoiceControls {...p} />);
    await waitFor(() => expect(p.onAvailability).toHaveBeenLastCalledWith(false));
    expect(screen.queryByRole("button", { name: "Talk to Agent" })).not.toBeInTheDocument();
    expect(mocks.instances[0].start).not.toHaveBeenCalled();
  });

  it("keeps the live mic when voice is available and reports it", async () => {
    config.voiceConfig.mockResolvedValue({ available: true, provider: "gpt_live", maximum_seconds: 180, maximum_turns: 6 });
    const p = { ...props(), onAvailability: vi.fn() };
    render(<RealtimeVoiceControls {...p} />);
    await waitFor(() => expect(p.onAvailability).toHaveBeenLastCalledWith(true));
    expect(screen.getByRole("button", { name: "Talk to Agent" })).toBeEnabled();
  });

  it("keeps today's behaviour when availability cannot be read", async () => {
    config.voiceConfig.mockRejectedValue(new Error("offline"));
    const p = { ...props(), onAvailability: vi.fn() };
    render(<RealtimeVoiceControls {...p} />);
    await waitFor(() => expect(config.voiceConfig).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Talk to Agent" })).toBeInTheDocument();
    expect(p.onAvailability).not.toHaveBeenCalledWith(false);
  });

  it("does not read availability while signed out", () => {
    render(<RealtimeVoiceControls {...props()} authenticated={false} />);
    expect(config.voiceConfig).not.toHaveBeenCalled();
  });

  it("shows time and turns left while listening", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-27T10:00:00Z"));
    render(<RealtimeVoiceControls {...props()} />);
    snapshot({ ...active, endsAt: Date.now() + 161_000, turnsLeft: 4 });
    expect(screen.getByRole("status")).toHaveTextContent("Listening");
    expect(screen.getByRole("dialog")).toHaveTextContent("GPT Live · 2:41 left · 4 turns left");
    act(() => { vi.advanceTimersByTime(2000); });
    expect(screen.getByRole("dialog")).toHaveTextContent("2:39 left");
    snapshot({ ...active, endsAt: Date.now() + 1_000, turnsLeft: 1 });
    expect(screen.getByRole("dialog")).toHaveTextContent("0:01 left · 1 turn left");
    vi.useRealTimers();
  });

  it("marks a limit stop distinctly from other errors", () => {
    render(<RealtimeVoiceControls {...props()} />);
    snapshot({ ...active, state: "error", active: false, limit: true, error: "Daily voice limit reached. Continue typing." });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveAttribute("data-limit", "true");
    expect(alert).toHaveTextContent("Limit reached · Daily voice limit reached. Continue typing.");
    snapshot({ ...active, state: "error", active: false, limit: false, error: "Voice connection failed." });
    expect(screen.getByRole("alert")).not.toHaveAttribute("data-limit");
  });
});
