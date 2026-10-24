import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RealtimeVoiceControls } from "./RealtimeVoiceControls";
import type { RealtimeSnapshot, RealtimeVoiceController } from "@/lib/voice/realtime-controller";

type Options = ConstructorParameters<typeof RealtimeVoiceController>[0];
const mocks = vi.hoisted(() => ({ instances: [] as Array<{
  options: Options; start: ReturnType<typeof vi.fn>; end: ReturnType<typeof vi.fn>;
  interrupt: ReturnType<typeof vi.fn>; resume: ReturnType<typeof vi.fn>;
  dispose: ReturnType<typeof vi.fn>; setConversation: ReturnType<typeof vi.fn>;
}> }));

vi.mock("@/lib/voice/realtime-controller", () => ({
  RealtimeVoiceController: class {
    start = vi.fn(); end = vi.fn(); interrupt = vi.fn(); resume = vi.fn();
    dispose = vi.fn(); setConversation = vi.fn();
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

  it("connects existing callbacks and exposes explicit interrupt, resume and end controls", () => {
    const p = props(); render(<RealtimeVoiceControls {...p} />); const voice = mocks.instances[0];
    fireEvent.click(screen.getByRole("button", { name: "Talk to Agent" })); expect(voice.start).toHaveBeenCalledOnce();
    snapshot(active); expect(screen.getByRole("status")).toHaveTextContent("GPT Live · Listening");
    fireEvent.click(screen.getByRole("button", { name: "Interrupt" })); expect(voice.interrupt).toHaveBeenCalledOnce();
    snapshot({ ...active, outputMuted: true });
    expect(screen.getByRole("status")).toHaveTextContent("Audio muted");
    fireEvent.click(screen.getByRole("button", { name: "Resume audio" })); expect(voice.resume).toHaveBeenCalledOnce();
    snapshot({ ...active, outputMuted: false });
    expect(screen.queryByRole("button", { name: "Resume audio" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "End voice" })); expect(voice.end).toHaveBeenCalledOnce();
    expect(p.onActive).toHaveBeenCalledWith(true);
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
