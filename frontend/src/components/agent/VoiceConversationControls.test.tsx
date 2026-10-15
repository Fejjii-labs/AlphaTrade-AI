import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { VoiceConversationControls } from "./VoiceConversationControls";
import type { VoiceProvider } from "@/lib/voice/types";
import type { VoiceAgentTransport } from "@/lib/voice/conversation-types";

let input: Parameters<VoiceProvider["listen"]>[0];
let output: Parameters<VoiceProvider["speak"]>[1];
const cancel = vi.fn();
const provider: VoiceProvider = {
  capabilities: { input: true, output: true },
  listen: vi.fn(events => { input = events; events.onState("listening"); return { stop: () => events.onState("transcribing"), cancel }; }),
  speak: vi.fn((_text, events) => { output = events; events.onStart(); return { stop: cancel, cancel }; }),
  dispose: vi.fn(),
};
const createProvider = () => provider;
const transport: VoiceAgentTransport = {
  submit: vi.fn(async () => ({ outcome: "acknowledged" as const, reply: "Risk reviewed." })),
  recover: vi.fn(async () => ({ outcome: "acknowledged" as const, reply: "Recovered." })),
};
const props = { conversationId: "c1", sessionKey: "user-1", authenticated: true, typedDraft: "", onTypedDraftChange: vi.fn(), transport, createProvider };
const conversation = () => {
  fireEvent.click(screen.getByRole("checkbox", { name: "Conversation mode" }));
  fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));
};

describe("compact voice conversation controls", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(transport.submit).mockResolvedValue({ outcome: "acknowledged", reply: "Risk reviewed." });
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); });

  it("mounts without microphone access; opt-in conversation plays and can stop immediately", async () => {
    render(<VoiceConversationControls {...props} />);
    expect(provider.listen).not.toHaveBeenCalled();
    expect(screen.getByRole("checkbox", { name: "Conversation mode" })).not.toBeChecked();
    conversation();
    expect(screen.getByRole("status")).toHaveTextContent("Listening");
    await act(async () => input.onComplete("Review risk"));
    expect(screen.getByRole("button", { name: "Stop speaking" })).toBeEnabled();
    const late = output;
    fireEvent.click(screen.getByRole("button", { name: "Stop speaking" }));
    act(() => late.onEnd());
    expect(provider.listen).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("status")).toHaveTextContent("Paused");
  });

  it("preserves typed text and offers deliberate append, replace and send with editable review", () => {
    const view = render(<VoiceConversationControls {...props} typedDraft="Keep my typed text" />);
    conversation(); act(() => input.onComplete("Voice text"));
    expect(transport.submit).not.toHaveBeenCalled();
    expect(props.onTypedDraftChange).not.toHaveBeenCalled();
    fireEvent.change(screen.getByRole("textbox", { name: "Voice transcript" }), { target: { value: "Edited voice" } });
    fireEvent.click(screen.getByRole("button", { name: "Append to draft" }));
    expect(props.onTypedDraftChange).toHaveBeenCalledWith("Keep my typed text\nEdited voice");
    view.rerender(<VoiceConversationControls {...props} typedDraft="New typed text" />);
    fireEvent.click(screen.getByRole("button", { name: "Start conversation" }));
    act(() => input.onComplete("Replacement"));
    fireEvent.click(screen.getByRole("button", { name: "Replace draft" }));
    expect(props.onTypedDraftChange).toHaveBeenLastCalledWith("Replacement");
  });

  it("keeps the review editor mounted when cleared and allows a replacement", async () => {
    render(<VoiceConversationControls {...props} typedDraft="Keep typed" />);
    conversation(); act(() => input.onComplete("Voice text"));
    const editor = screen.getByRole("textbox", { name: "Voice transcript" });
    fireEvent.change(editor, { target: { value: "" } });
    expect(screen.getByRole("textbox", { name: "Voice transcript" })).toBe(editor);
    expect(screen.getByRole("button", { name: "Send transcript" })).toBeDisabled();
    fireEvent.change(editor, { target: { value: "Replacement speech" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send transcript" })));
    expect(transport.submit).toHaveBeenCalledWith(expect.objectContaining({ transcript: "Replacement speech" }));
    expect(props.onTypedDraftChange).not.toHaveBeenCalled();
  });

  it("creates the conversation through the supplied flow before starting capture", async () => {
    let finish!: (id: string) => void;
    const ensureConversation = vi.fn(() => new Promise<string>(resolve => { finish = resolve; }));
    render(<VoiceConversationControls {...props} conversationId={null} ensureConversation={ensureConversation} />);
    fireEvent.click(screen.getByRole("button", { name: "Record" }));
    expect(provider.listen).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Preparing conversation…" })).toBeDisabled();
    await act(async () => finish("created"));
    expect(provider.listen).toHaveBeenCalledOnce();
  });

  it("cancels conversation creation on navigation and ignores its late result", async () => {
    let finish!: (id: string) => void;
    const ensureConversation = vi.fn<(signal: AbortSignal) => Promise<string>>(() => new Promise<string>(resolve => { finish = resolve; }));
    const view = render(<VoiceConversationControls {...props} conversationId={null} ensureConversation={ensureConversation} />);
    fireEvent.click(screen.getByRole("button", { name: "Record" }));
    view.rerender(<VoiceConversationControls {...props} conversationId="other" contextKey={1} ensureConversation={ensureConversation} />);
    expect(ensureConversation.mock.calls[0][0].aborted).toBe(true);
    await act(async () => finish("created"));
    expect(provider.listen).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Record" })).toBeEnabled();
  });

  it("cancels recording and ignores delayed transcripts", () => {
    render(<VoiceConversationControls {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Record" }));
    const late = input; fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    act(() => late.onComplete("Late"));
    expect(screen.queryByRole("textbox", { name: "Voice transcript" })).toBeNull();
    expect(transport.submit).not.toHaveBeenCalled();
  });

  it("shows one actionable error and keeps browser diagnostics inside a disclosure", () => {
    render(<VoiceConversationControls {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Record" }));
    act(() => input.onError({ code: "permission", message: "Browser detail", sourceCode: "not-allowed" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Retry or type your message");
    expect(screen.getByText(/Browser detail/).closest("details")).not.toHaveAttribute("open");
    expect(screen.getByRole("button", { name: "Record" })).toBeEnabled();
  });

  it("retains an uncertain turn for explicit recovery and pauses automatic progression", async () => {
    vi.mocked(transport.submit).mockResolvedValue({ outcome: "uncertain" });
    render(<VoiceConversationControls {...props} />); conversation();
    await act(async () => input.onComplete("Recover this"));
    expect(screen.getByRole("button", { name: "Resume voice" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Voice transcript" })).toHaveValue("Recover this");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Recover original request" })));
    const submitted = vi.mocked(transport.submit).mock.calls[0][0];
    expect(transport.recover).toHaveBeenCalledWith(expect.objectContaining({ turnKey: submitted.turnKey, transcript: submitted.transcript, origin: "voice" }));
    expect(provider.listen).toHaveBeenCalledTimes(1);
  });

  it.each(["conversation", "identity", "logout", "disabled", "hidden", "unmount"])("cancels and fences late callbacks on %s", event => {
    const view = render(<VoiceConversationControls {...props} />); conversation(); const late = input;
    if (event === "conversation") view.rerender(<VoiceConversationControls {...props} conversationId="c2" />);
    if (event === "identity") view.rerender(<VoiceConversationControls {...props} sessionKey="user-2" />);
    if (event === "logout") view.rerender(<VoiceConversationControls {...props} authenticated={false} />);
    if (event === "disabled") view.rerender(<VoiceConversationControls {...props} disabled />);
    if (event === "unmount") view.unmount();
    if (event === "hidden") { vi.spyOn(document, "hidden", "get").mockReturnValue(true); fireEvent(document, new Event("visibilitychange")); }
    act(() => late.onComplete("Late"));
    expect(cancel).toHaveBeenCalled(); expect(transport.submit).not.toHaveBeenCalled();
  });

  it("keeps Cancel available while preparing an empty transcript", () => {
    render(<VoiceConversationControls {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Record" }));
    fireEvent.click(screen.getByRole("button", { name: "Stop recording" }));
    expect(screen.getByRole("status")).toHaveTextContent("Preparing transcript");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("button", { name: "Record" })).toBeEnabled();
  });

  it("unsupported input leaves the independent typed composer usable", () => {
    const factory = () => ({ ...provider, capabilities: { input: false, output: false } });
    render(<><textarea aria-label="Typed draft" defaultValue="Typed fallback" /><VoiceConversationControls {...props} createProvider={factory} /></>);
    expect(screen.getByRole("button", { name: "Record" })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: "Typed draft" })).toBeEnabled();
    expect(screen.getByText("Voice input unavailable. Type your message.")).toBeInTheDocument();
  });
});
