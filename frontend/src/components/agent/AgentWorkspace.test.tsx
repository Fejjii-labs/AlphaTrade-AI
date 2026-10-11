import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  AgentWorkspace,
  AGENT_TURN_TIMEOUT_MS,
} from "@/components/agent/AgentWorkspace";
import * as browserVoice from "@/lib/voice/browser-voice-provider";
import type { VoiceProvider } from "@/lib/voice/types";
import type { BrowserRecognition } from "@/lib/voice/browser-voice-provider";
import { agentTurnFixture } from "@/test/pilot-fixtures";
import { agentTurn } from "@/lib/api/generated/client";
import { clearPendingTurns, pendingFor, terminalFor } from "./turn-recovery";

const recoveryScope = "00000000-0000-0000-0000-000000000001:00000000-0000-0000-0000-000000000002";

let testParams = new URLSearchParams();
let testIdentity = { user: "00000000-0000-0000-0000-000000000002", organization: "00000000-0000-0000-0000-000000000001" };
vi.mock("next/navigation", () => ({
  useSearchParams: () => testParams,
}));

vi.mock("@/contexts/AuthContext", () => ({
  useAuth: () => ({ user: { id: testIdentity.user }, organization: { id: testIdentity.organization } }),
}));
const apiMocks = vi.hoisted(() => ({
  listConversations: vi.fn(),
  listMessages: vi.fn(),
  getConversation: vi.fn(),
  createConversation: vi.fn(),
  agentTurn: vi.fn(),
  confirmProposal: vi.fn(),
  rejectProposal: vi.fn(),
  listPositions: vi.fn(),
  listStrategies: vi.fn(),
  getStrategy: vi.fn(),
  marketStatus: vi.fn(),
  previewFile: vi.fn(),
  importFile: vi.fn(),
  ingest: vi.fn(),
  killSwitchActive: false,
}));

vi.mock("@/lib/api", () => ({
  api: {
    conversations: {
      list: apiMocks.listConversations,
      listMessages: apiMocks.listMessages,
      get: apiMocks.getConversation,
      create: apiMocks.createConversation,
    },
    chat: { message: vi.fn() },
    agent: {
      turn: apiMocks.agentTurn,
      confirmProposal: apiMocks.confirmProposal,
      rejectProposal: apiMocks.rejectProposal,
    },
    positions: { list: apiMocks.listPositions },
    strategies: { list: apiMocks.listStrategies, get: apiMocks.getStrategy },
    canonical: { getMarketStatus: apiMocks.marketStatus },
    knowledge: {
      previewFile: apiMocks.previewFile,
      importFile: apiMocks.importFile,
      ingest: apiMocks.ingest,
    },
  },
}));

vi.mock("@/contexts/AppContext", () => ({
  useAppContext: () => ({ killSwitchActive: apiMocks.killSwitchActive }),
}));

describe("Agent workspace", () => {
  beforeEach(() => {
    testIdentity = { user: "00000000-0000-0000-0000-000000000002", organization: "00000000-0000-0000-0000-000000000001" };
    clearPendingTurns();
    sessionStorage.clear();
    apiMocks.createConversation.mockResolvedValue({ id: "11111111-1111-4111-8111-111111111111" });
    apiMocks.getConversation.mockResolvedValue({ strategy_id: null });
    testParams = new URLSearchParams();
    apiMocks.listConversations.mockResolvedValue({
      items: [{ id: "11111111-1111-4111-8111-111111111111", title: "BTC plan" }],
      total: 1,
      limit: 30,
      offset: 0,
    });
    apiMocks.listMessages.mockResolvedValue({
      items: [
        {
          id: "m1",
          role: "user",
          content: "Is this a pullback?",
          created_at: "2026-01-01",
        },
        {
          id: "m2",
          role: "assistant",
          content: "Paper only: wait for confirmation.",
          created_at: "2026-01-01",
        },
      ],
      total: 2,
      limit: 100,
      offset: 0,
    });
    apiMocks.listPositions.mockResolvedValue({
      items: [{ id: "p1", symbol: "BTCUSDT", direction: "long" }],
      total: 1,
      limit: 20,
      offset: 0,
    });
    apiMocks.listStrategies.mockResolvedValue({
      items: [],
      total: 0,
      limit: 50,
      offset: 0,
    });
    apiMocks.marketStatus.mockResolvedValue({
      availability: "fresh",
      symbol: "BTCUSDT",
    });
    apiMocks.agentTurn.mockResolvedValue({
      user_message_id: "m1", assistant_message_id: "m2",
      conversation_id: "11111111-1111-4111-8111-111111111111",
      reply: "Noted.\n\nRecorded facts (not a confirmation):\nDraft only.",
      capability: "general_conversation",
      operation: "read",
      proposals: [],
      limitations: [],
      authority_mutated: false,
      execution_attempted: false,
      real_trading_enabled: false,
    });
    apiMocks.confirmProposal.mockResolvedValue({
      proposal_id: "p1",
      conversation_id: "11111111-1111-4111-8111-111111111111",
      kind: "propose_journal_entry",
      artifact_kind: "journal_entry",
      status: "applied",
      summary: "Journal draft",
      content_hash: "a".repeat(64),
      applied: true,
      authority_mutated: true,
    });
  });

  afterEach(() => {
    cleanup();
    vi.clearAllMocks();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    apiMocks.killSwitchActive = false;
  });

  it("opens the current tail beyond one API page and explicitly retains older messages", async () => {
    testParams.set("conversation", "11111111-1111-4111-8111-111111111111");
    const messages = Array.from({ length: 230 }, (_, index) => ({
      id: `page-message-${index}`, role: "assistant", content: `History ${index}`,
      created_at: new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString(),
    }));
    apiMocks.listMessages.mockImplementation(async (_id, params) => ({
      items: messages.slice(params.offset ?? 0, (params.offset ?? 0) + 100),
      total: messages.length, limit: 100, offset: params.offset ?? 0,
    }));
    render(<AgentWorkspace />);
    await screen.findByText("History 229");
    expect(screen.queryByText("History 0")).not.toBeInTheDocument();
    expect(apiMocks.listMessages).toHaveBeenLastCalledWith(expect.any(String), { limit: 100, offset: 130 }, expect.objectContaining({ signal: expect.any(AbortSignal) }));
    fireEvent.click(screen.getByRole("button", { name: "Earlier messages" }));
    await screen.findByText("History 30");
    expect(screen.getByText("History 229")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Earlier messages" }));
    await screen.findByText("History 0");
    expect(screen.getAllByTestId("agent-message")).toHaveLength(230);
    expect(screen.queryByRole("button", { name: "Earlier messages" })).not.toBeInTheDocument();
  });

  it("releases an earlier request superseded by turn reconciliation without releasing its replacement", async () => {
    const id = "11111111-1111-4111-8111-111111111111";
    testParams.set("conversation", id);
    const messages = Array.from({ length: 130 }, (_, index) => ({
      id: `overlap-${index}`, role: "assistant", content: `Overlap history ${index}`,
      created_at: new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString(),
    }));
    let acknowledge!: (value: unknown) => void;
    let staleEarlier!: (value: unknown) => void;
    let replacementEarlier!: (value: unknown) => void;
    let earlierCalls = 0;
    let staleSignal!: AbortSignal;
    apiMocks.agentTurn.mockImplementationOnce(() => new Promise(resolve => { acknowledge = resolve; }));
    apiMocks.listMessages.mockImplementation(async (_id, params, options) => {
      if (params.offset === 0) {
        earlierCalls++;
        if (earlierCalls === 1) {
          staleSignal = options.signal;
          return new Promise(resolve => { staleEarlier = resolve; });
        }
        return new Promise(resolve => { replacementEarlier = resolve; });
      }
      return { items: messages.slice(params.offset ?? 0, (params.offset ?? 0) + 100), total: 130, limit: 100, offset: params.offset ?? 0 };
    });
    render(<AgentWorkspace />);
    await screen.findByText("Overlap history 129");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "New intentional turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(screen.getByRole("button", { name: "Earlier messages" }));
    await screen.findByRole("button", { name: "Loading earlier messages…" });
    await act(async () => acknowledge({ conversation_id: id, user_message_id: "overlap-user", assistant_message_id: "overlap-reply", reply: "Acknowledged during paging", proposals: [] }));
    await screen.findByText("Acknowledged during paging");
    expect(staleSignal.aborted).toBe(true);
    const earlier = await screen.findByRole("button", { name: "Earlier messages" });
    await waitFor(() => expect(earlier).toBeEnabled());
    fireEvent.click(earlier);
    await waitFor(() => expect(earlierCalls).toBe(2));
    await act(async () => staleEarlier({ items: [{ id: "stale-page", role: "assistant", content: "Stale aborted page", created_at: "2026-01-01" }], total: 130 }));
    expect(screen.getByRole("button", { name: "Loading earlier messages…" })).toBeDisabled();
    expect(screen.queryByText("Stale aborted page")).not.toBeInTheDocument();
    await act(async () => replacementEarlier({ items: messages.slice(0, 100), total: 130 }));
    await screen.findByText("Overlap history 0");
    expect(screen.getByText("Overlap history 129")).toBeInTheDocument();
    expect(screen.getByText("Acknowledged during paging")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Earlier messages" })).not.toBeInTheDocument();
  });

  it.each(["conversation", "account"])("keeps earlier loading and private content owned by the current %s", async change => {
    testParams.set("conversation", "first");
    const items = (prefix: string) => Array.from({ length: 130 }, (_, index) => ({
      id: `${prefix}-${index}`, role: "assistant", content: `${prefix} history ${index}`,
      created_at: new Date(Date.UTC(2026, 0, 1, 0, 0, index)).toISOString(),
    }));
    let finishOld!: (value: unknown) => void;
    let finishNew!: (value: unknown) => void;
    let oldSignal!: AbortSignal;
    apiMocks.listMessages.mockImplementation(async (id, params, options) => {
      if (params.offset === 0) {
        if (id === "first") {
          oldSignal = options.signal;
          return new Promise(resolve => { finishOld = resolve; });
        }
        return new Promise(resolve => { finishNew = resolve; });
      }
      return { items: items(id).slice(params.offset ?? 0, (params.offset ?? 0) + 100), total: 130, limit: 100, offset: params.offset ?? 0 };
    });
    const { rerender } = render(<AgentWorkspace />);
    await screen.findByText("first history 129");
    fireEvent.click(screen.getByRole("button", { name: "Earlier messages" }));
    await screen.findByRole("button", { name: "Loading earlier messages…" });
    testParams = new URLSearchParams("conversation=second");
    if (change === "account") testIdentity = { user: "00000000-0000-0000-0000-000000000004", organization: "00000000-0000-0000-0000-000000000003" };
    rerender(<AgentWorkspace />);
    await screen.findByText("second history 129");
    expect(oldSignal.aborted).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Earlier messages" }));
    await screen.findByRole("button", { name: "Loading earlier messages…" });
    await act(async () => finishOld({ items: items("private-old").slice(0, 100), total: 130 }));
    expect(screen.getByRole("button", { name: "Loading earlier messages…" })).toBeDisabled();
    expect(screen.queryByText("private-old history 0")).not.toBeInTheDocument();
    await act(async () => finishNew({ items: items("second").slice(0, 100), total: 130 }));
    await screen.findByText("second history 0");
    expect(screen.queryByRole("button", { name: "Earlier messages" })).not.toBeInTheDocument();
    if (change === "account") {
      testParams = new URLSearchParams("conversation=first");
      apiMocks.listMessages.mockRejectedValue(new Error("New account cannot read that conversation"));
      rerender(<AgentWorkspace />);
      await screen.findByText(/New account cannot read that conversation/);
      expect(screen.queryByText("first history 129")).not.toBeInTheDocument();
    }
  });

  function enableVoice(text: string) {
    const provider: VoiceProvider = {
      capabilities: { input: true, output: true },
      listen: vi.fn((callbacks) => {
        callbacks.onComplete(text);
        return { stop: vi.fn(), cancel: vi.fn() };
      }),
      speak: vi.fn(() => ({ stop: vi.fn(), cancel: vi.fn() })),
      dispose: vi.fn(),
    };
    vi.spyOn(browserVoice, "createBrowserVoiceProvider").mockReturnValue(
      provider,
    );
    return provider;
  }

  it("allows typed submission after a real browser recognition transcription error", async () => {
    const recognitions: BrowserRecognition[] = [];
    class Recognition implements BrowserRecognition {
      lang = ""; continuous = false; interimResults = false;
      onstart: BrowserRecognition["onstart"] = null;
      onend: BrowserRecognition["onend"] = null;
      onerror: BrowserRecognition["onerror"] = null;
      onresult: BrowserRecognition["onresult"] = null;
      start() { this.onstart?.(); }
      stop() {}
      abort() {}
      constructor() { recognitions.push(this); }
    }
    vi.stubGlobal("SpeechRecognition", Recognition);
    vi.stubGlobal("isSecureContext", true);
    render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Keep my typed reflection" } });
    await waitFor(() => expect(screen.getByRole("button", { name: "Record" })).toBeEnabled());
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Record" })));
    act(() => recognitions[0].onerror?.({ error: "network" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Retry or type your message");
    expect(screen.getByLabelText("Message")).toHaveValue("Keep my typed reflection");
    expect(apiMocks.agentTurn).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(apiMocks.agentTurn).toHaveBeenCalledWith(expect.objectContaining({ message: "Keep my typed reflection" }), expect.anything()));
    expect(screen.getByTestId("agent-thread")).not.toHaveClass("overflow-y-auto");
  });

  it("keeps the actual composer usable when the browser offers no speech recognition", async () => {
    vi.stubGlobal("SpeechRecognition", undefined);
    vi.stubGlobal("webkitSpeechRecognition", undefined);
    render(<AgentWorkspace />);
    expect(await screen.findByText("Voice input unavailable. Type your message.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Record" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Typed fallback" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(apiMocks.agentTurn).toHaveBeenCalledWith(expect.objectContaining({ message: "Typed fallback" }), expect.anything()));
  });

  it("renders an acknowledged saved summary immediately and restores its link from durable history", async () => {
    const record = {
      id: "owned-saved-rule", category: "rules", title: "Wait for a close", summary: "Wait for a closed confirmation.",
      revision: 1, conversation_id: agentTurnFixture.conversation_id, source_message_ids: ["m3"],
      source_document_id: null, trade_id: null, tags: [], original_text: "My rule: wait for a close.",
      created_at: "2026-10-11", updated_at: "2026-10-11", draft: null, undone: false,
    };
    apiMocks.listMessages.mockResolvedValue({ items: [] });
    apiMocks.agentTurn.mockResolvedValue({
      ...agentTurnFixture, user_message_id: "m3", assistant_message_id: "m4", reply: "Your rule is saved.",
      saved_entries: [record], capture_status: "saved", capture_source_message_id: "m3",
    });
    const view = render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: record.original_text } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const target = "/journal?tab=knowledge&category=rules&saved=owned-saved-rule";
    expect(await screen.findByRole("link", { name: "Open Wait for a close in Rules" })).toHaveAttribute("href", target);
    expect(screen.getByText("Your rule is saved.")).toBeInTheDocument();
    view.unmount();
    testParams = new URLSearchParams(`conversation=${agentTurnFixture.conversation_id}`);
    apiMocks.listMessages.mockResolvedValue({ items: [
      { id: "m3", role: "user", content: record.original_text, created_at: "2026-10-11" },
      { id: "m4", role: "assistant", content: "Your rule is saved.", created_at: "2026-10-11",
        payload: { interactive_agent: { capture: { status: "saved", saved_entries: [record], source_message_id: "m3" } } } },
    ] });
    render(<AgentWorkspace />);
    expect(await screen.findByRole("link", { name: "Open Wait for a close in Rules" })).toHaveAttribute("href", target);
    expect(screen.getByText("Your rule is saved.")).toBeInTheDocument();
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(1);
  });

  function interactiveVoice() {
    let input!: Parameters<VoiceProvider["listen"]>[0];
    let output!: Parameters<VoiceProvider["speak"]>[1];
    const cancelInput = vi.fn();
    const cancelOutput = vi.fn();
    const provider: VoiceProvider = {
      capabilities: { input: true, output: true },
      listen: vi.fn(events => { input = events; events.onState("listening"); return { stop: () => events.onState("transcribing"), cancel: cancelInput }; }),
      speak: vi.fn((_text, events) => { output = events; events.onStart(); return { stop: cancelOutput, cancel: cancelOutput }; }),
      dispose: vi.fn(),
    };
    vi.spyOn(browserVoice, "createBrowserVoiceProvider").mockReturnValue(provider);
    return { provider, input: () => input, output: () => output, cancelInput, cancelOutput };
  }

  async function beginConversationVoice() {
    fireEvent.click(screen.getByRole("checkbox", { name: "Conversation mode" }));
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Start conversation" })));
  }

  it("runs two successive spoken turns from a new conversation through the shared pipeline", async () => {
    const speech = interactiveVoice();
    apiMocks.listMessages.mockResolvedValue({ items: [] });
    apiMocks.agentTurn.mockResolvedValueOnce({ ...agentTurnFixture, reply: "First spoken reply" })
      .mockResolvedValueOnce({ ...agentTurnFixture, user_message_id: "55555555-5555-4555-8555-555555555555", assistant_message_id: "66666666-6666-4666-8666-666666666666", reply: "Second spoken reply" });
    render(<AgentWorkspace />);
    expect(speech.provider.listen).not.toHaveBeenCalled();
    await beginConversationVoice();
    expect(apiMocks.createConversation).toHaveBeenCalledExactlyOnceWith({});
    const first = speech.input();
    act(() => { first.onTranscript("Partial"); first.onTranscript("Partial"); });
    expect(apiMocks.agentTurn).not.toHaveBeenCalled();
    await act(async () => { first.onComplete("First spoken turn"); first.onComplete("First spoken turn"); });
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(1);
    expect(speech.provider.speak).toHaveBeenCalledWith("First spoken reply", expect.any(Object));
    expect(speech.provider.listen).toHaveBeenCalledTimes(1);
    expect(screen.getByText("First spoken reply")).toBeVisible();
    act(() => speech.output().onEnd());
    expect(speech.provider.listen).toHaveBeenCalledTimes(2);
    await act(async () => speech.input().onComplete("Second spoken turn"));
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(2);
    expect(speech.provider.speak).toHaveBeenCalledWith("Second spoken reply", expect.any(Object));
    expect(screen.getByText("Second spoken reply")).toBeVisible();
    const keys = apiMocks.agentTurn.mock.calls.map(call => call[1].headers["Idempotency-Key"]);
    expect(keys[0]).not.toBe(keys[1]);
    expect(terminalFor(recoveryScope, keys[0])).toMatchObject({ outcome: "acknowledged", origin: "voice" });
    expect(apiMocks.agentTurn.mock.calls[0][0]).toEqual(expect.objectContaining({ message: "First spoken turn", conversation_id: agentTurnFixture.conversation_id }));
    expect(apiMocks.agentTurn.mock.calls[0][0]).not.toHaveProperty("origin");
    expect(apiMocks.confirmProposal).not.toHaveBeenCalled();
    expect(screen.getAllByRole("region", { name: "Voice conversation" })).toHaveLength(1);
    expect(screen.queryByRole("region", { name: "Voice controls" })).toBeNull();
  });

  it("clears and replaces reviewed speech, preserving typed drafts and local voice provenance", async () => {
    const speech = interactiveVoice();
    let finish!: (result: unknown) => void;
    apiMocks.agentTurn.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Keep typed draft" } });
    await beginConversationVoice();
    act(() => speech.input().onComplete("Original speech"));
    const editor = screen.getByLabelText("Voice transcript");
    fireEvent.change(editor, { target: { value: "" } });
    expect(screen.getByLabelText("Voice transcript")).toBe(editor);
    expect(screen.getByRole("button", { name: "Send transcript" })).toBeDisabled();
    fireEvent.change(editor, { target: { value: "Replacement speech" } });
    fireEvent.click(screen.getByRole("button", { name: "Send transcript" }));
    const saved = pendingFor(recoveryScope, agentTurnFixture.conversation_id)!;
    expect(saved.origin).toBe("voice");
    expect(saved.voice).toMatchObject({ turnKey: saved.key, transcript: "Replacement speech", conversationId: agentTurnFixture.conversation_id });
    expect(apiMocks.agentTurn.mock.calls[0][1].headers["Idempotency-Key"]).toBe(saved.key);
    expect(screen.getByRole("button", { name: "Sending…" })).toBeDisabled();
    await act(async () => finish(agentTurnFixture));
    expect(screen.getByLabelText("Message")).toHaveValue("Keep typed draft");
    expect(pendingFor(recoveryScope, agentTurnFixture.conversation_id)).toBeNull();
  });

  it("rejects an invalid spoken body locally without saving an ambiguous request", async () => {
    const speech = interactiveVoice(); render(<AgentWorkspace />); await beginConversationVoice();
    const transcript = "x".repeat(8001);
    await act(async () => speech.input().onComplete(transcript));
    expect(apiMocks.agentTurn).not.toHaveBeenCalled();
    expect(pendingFor(recoveryScope, agentTurnFixture.conversation_id)).toBeNull();
    expect(screen.getByLabelText("Voice transcript")).toHaveValue(transcript);
    expect(screen.getByRole("button", { name: "Send transcript" })).toBeEnabled();
  });

  it("shows an acknowledged reply with pending capture but retains the original voice request for recovery", async () => {
    const speech = interactiveVoice();
    apiMocks.agentTurn.mockResolvedValueOnce({ ...agentTurnFixture, capture_status: "unavailable", capture_error: "Capture pending; recover original turn" });
    render(<AgentWorkspace />); await beginConversationVoice();
    await act(async () => speech.input().onComplete("Capture this spoken note"));
    expect(screen.getByText(agentTurnFixture.reply)).toBeVisible();
    const saved = pendingFor(recoveryScope, agentTurnFixture.conversation_id)!;
    expect(saved.state).toBe("turn_capture");
    expect(terminalFor(recoveryScope, saved.key)).toBeNull();
    expect(speech.provider.speak).not.toHaveBeenCalled();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Recover original request" })));
    expect(apiMocks.agentTurn.mock.calls[1][0]).toEqual(saved.body);
    expect(apiMocks.agentTurn.mock.calls[1][1].headers["Idempotency-Key"]).toBe(saved.key);
    expect(pendingFor(recoveryScope, agentTurnFixture.conversation_id)).toBeNull();
    expect(speech.provider.listen).toHaveBeenCalledOnce();
    expect(screen.getByRole("button", { name: /Resume voice|Start conversation/ })).toBeEnabled();
  });

  it("recovers an uncertain voice request exactly once after navigation and remount with original attachment/body/key", async () => {
    const speech = interactiveVoice();
    testParams = new URLSearchParams(`conversation=${agentTurnFixture.conversation_id}`);
    apiMocks.listMessages.mockResolvedValue({ items: [] });
    apiMocks.previewFile.mockResolvedValue({ title: "rules", extracted_text: "Rules", warnings: [], preview_receipt: "receipt" });
    apiMocks.importFile.mockResolvedValue({ document_id: "44444444-4444-4444-8444-444444444444" });
    apiMocks.agentTurn.mockRejectedValueOnce(new Error("Response uncertain"));
    const first = render(<AgentWorkspace />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Record" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "Attach document" }));
    fireEvent.change(screen.getByLabelText("Attach document"), { target: { files: [new File(["Rules"], "rules.txt")] } });
    fireEvent.click(screen.getByRole("button", { name: "Preview attachment" }));
    await screen.findByLabelText("Attachment preview");
    await beginConversationVoice();
    await act(async () => speech.input().onComplete("Review rules"));
    const original = apiMocks.agentTurn.mock.calls[0];
    expect(original[0].source_document_id).toBe("44444444-4444-4444-8444-444444444444");
    testParams = new URLSearchParams("conversation=77777777-7777-4777-8777-777777777777");
    first.rerender(<AgentWorkspace />);
    await waitFor(() => expect(screen.queryByTestId("agent-turn-recovery")).toBeNull());
    first.unmount();
    testParams = new URLSearchParams(`conversation=${agentTurnFixture.conversation_id}`);
    render(<AgentWorkspace />);
    await screen.findByRole("button", { name: "Recover original request" });
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Separate typed draft" } });
    const recover = screen.getByRole("button", { name: "Recover original request" });
    await act(async () => { fireEvent.click(recover); fireEvent.click(recover); });
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(2);
    expect(apiMocks.agentTurn.mock.calls[1][0]).toEqual(original[0]);
    expect(apiMocks.agentTurn.mock.calls[1][1].headers).toEqual(original[1].headers);
    expect(apiMocks.importFile).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("Message")).toHaveValue("Separate typed draft");
    expect(screen.queryByTestId("agent-turn-recovery")).toBeNull();
  });

  it("a proven late acknowledgment for A clears the matching voice fence when returning from B", async () => {
    const speech = interactiveVoice();
    const id = agentTurnFixture.conversation_id;
    testParams = new URLSearchParams(`conversation=${id}`);
    apiMocks.listMessages.mockResolvedValue({ items: [] });
    let finish!: (result: unknown) => void;
    apiMocks.agentTurn.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const view = render(<AgentWorkspace />);
    await waitFor(() => expect(screen.getByRole("button", { name: "Record" })).toBeEnabled());
    await beginConversationVoice(); act(() => speech.input().onComplete("A spoken turn"));
    const saved = pendingFor(recoveryScope, id)!;
    testParams = new URLSearchParams("conversation=77777777-7777-4777-8777-777777777777");
    view.rerender(<AgentWorkspace />);
    await act(async () => finish({ ...agentTurnFixture, reply: "Acknowledged A" }));
    expect(screen.queryByText("Acknowledged A")).toBeNull();
    expect(terminalFor(recoveryScope, saved.key)).toMatchObject({ conversationId: id, outcome: "acknowledged" });
    testParams = new URLSearchParams(`conversation=${id}`); view.rerender(<AgentWorkspace />);
    await screen.findByText("Acknowledged A");
    expect(screen.queryByRole("button", { name: "Recover original request" })).toBeNull();
    const resume = screen.getByRole("button", { name: /Start conversation|Resume voice/ });
    expect(resume).toBeEnabled(); fireEvent.click(resume);
    expect(speech.provider.listen).toHaveBeenCalledTimes(2);
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(1);
  });

  it("uses the six-minute Agent budget and keeps exact ambiguous recovery when storage disappears", async () => {
    const speech = interactiveVoice();
    apiMocks.agentTurn.mockImplementationOnce(() => new Promise(() => {}));
    render(<AgentWorkspace />); await beginConversationVoice();
    vi.useFakeTimers(); act(() => speech.input().onComplete("Budgeted turn"));
    const original = apiMocks.agentTurn.mock.calls[0];
    await act(async () => vi.advanceTimersByTimeAsync(45_000));
    expect(original[1].signal.aborted).toBe(false);
    expect(screen.getByRole("region", { name: "Voice conversation" })).toHaveAttribute("data-state", "waiting");
    sessionStorage.clear();
    await act(async () => vi.advanceTimersByTimeAsync(AGENT_TURN_TIMEOUT_MS - 45_000));
    expect(original[1].signal.aborted).toBe(true);
    expect(pendingFor(recoveryScope, agentTurnFixture.conversation_id)?.body).toEqual(original[0]);
    expect(screen.getByRole("button", { name: "Recover original request" })).toBeEnabled();
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(1);
  });

  it("typed submission shares admission and stops recognition without submitting a late voice callback", async () => {
    const speech = interactiveVoice(); render(<AgentWorkspace />); await beginConversationVoice();
    const late = speech.input();
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Typed turn" } });
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Send" })));
    act(() => late.onComplete("Late voice"));
    expect(speech.cancelInput).toHaveBeenCalled();
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(1);
    expect(apiMocks.agentTurn.mock.calls[0][0].message).toBe("Typed turn");
  });

  it("logout stops the only microphone and clears user-scoped pending provenance", async () => {
    const speech = interactiveVoice(); render(<AgentWorkspace />); await beginConversationVoice();
    const late = speech.input();
    const { sessionCleared } = await import("@/lib/auth/session-events");
    act(() => sessionCleared()); act(() => late.onComplete("Private speech"));
    expect(speech.cancelInput).toHaveBeenCalled();
    expect(speech.provider.dispose).toHaveBeenCalled();
    expect(apiMocks.agentTurn).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Record" })).toBeDisabled();
  });

  it("collapses evidence in existing assistant transcripts and preserves user text", async () => {
    const marker = "\n\nRecorded facts (not a confirmation):\n";
    apiMocks.listMessages.mockResolvedValue({
      items: [
        {
          id: "u",
          role: "user",
          created_at: "2026-01-01",
          content: `My quotation${marker}Keep this inline.`,
        },
        {
          id: "a",
          role: "assistant",
          created_at: "2026-01-01",
          content: `Conclusion: wait.\n\nStored evidence is stale; it cannot establish a current price.${marker}version=raw-id; hash=abc123`,
        },
      ],
    });
    render(<AgentWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: "BTC plan" }));
    const summary = await screen.findByText("Stored evidence");
    const details = summary.closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(summary.tagName).toBe("SUMMARY");
    expect(details).toHaveTextContent("version=raw-id; hash=abc123");
    const messages = screen.getAllByTestId("agent-message");
    expect(messages[0].querySelector("details")).toBeNull();
    expect(messages[0]).toHaveTextContent("Keep this inline.");
    expect(screen.getByText(/Conclusion: wait/).closest("details")).toBeNull();
    expect(screen.getByText(/Stored evidence is stale/)).toBeVisible();
    fireEvent.click(summary);
    await waitFor(() => expect(details).toHaveAttribute("open"));
  });

  it("renders full stored evidence when a new transcript contains only an excerpt", async () => {
    apiMocks.listMessages.mockResolvedValue({
      items: [
        {
          id: "a",
          role: "assistant",
          created_at: "2026-01-01",
          content:
            "Conclusion: wait.\n\nRecorded facts (not a confirmation):\nEvidence excerpt.",
          payload: {
            interactive_agent: {
              recorded_evidence:
                "Nested approved. SFP approved. Full reference raw-id.",
            },
          },
        },
      ],
    });
    render(<AgentWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: "BTC plan" }));
    const details = (await screen.findByText("Stored evidence")).closest(
      "details",
    )!;
    expect(details).toHaveTextContent(
      "Nested approved. SFP approved. Full reference raw-id.",
    );
    expect(details).not.toHaveTextContent("Evidence excerpt.");
    expect(details).not.toHaveAttribute("open");
  });

  it("presents bounded manual command choices as visible durable links", async () => {
    apiMocks.listMessages.mockResolvedValue({
      items: [
        {
          id: "choices",
          role: "assistant",
          created_at: "2026-10-08",
          content:
            "Multiple manual BloFin demo orders match. Select an exact command.",
          payload: {
            interactive_agent: {
              sources: [
                {
                  relation: "manual demo choice",
                  record_id: "original-command",
                  title: "12:56 UTC | 0.1 contracts | filled",
                },
                {
                  relation: "manual demo choice",
                  record_id: "blocked-command",
                  title: "13:02 UTC | 1 contracts | blocked",
                },
              ],
            },
          },
        },
      ],
    });
    render(<AgentWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: "BTC plan" }));
    const choices = await screen.findByRole("list", {
      name: "Matching manual demo attempts",
    });
    expect(choices).toHaveTextContent("0.1 contracts | filled");
    expect(choices).toHaveTextContent("1 contracts | blocked");
    expect(
      screen.getByRole("link", { name: "12:56 UTC | 0.1 contracts | filled" }),
    ).toHaveAttribute("href", "/execution/manual-demo/original-command");
    expect(
      screen.getByRole("link", { name: "13:02 UTC | 1 contracts | blocked" }),
    ).toHaveAttribute("href", "/execution/manual-demo/blocked-command");
    expect(apiMocks.agentTurn).not.toHaveBeenCalled();
  });

  it("keeps full evidence available when the post-turn history request fails", async () => {
    apiMocks.listMessages.mockRejectedValue(new Error("History unavailable"));
    apiMocks.agentTurn.mockResolvedValue({
      user_message_id: "m1", assistant_message_id: "m2",
      conversation_id: "11111111-1111-4111-8111-111111111111",
      reply:
        "Conclusion: wait.\n\nRecorded facts (not a confirmation):\nExcerpt.",
      recorded_evidence: "Full governed evidence beyond the reply budget.",
      full_reply: "Full additional explanation after the clean display ending.",
      connections: [{ title: "TradePlan", record_id: "plan-ref" }],
      capability: "general_conversation",
      operation: "read",
      proposals: [],
      limitations: [],
      authority_mutated: false,
      execution_attempted: false,
      real_trading_enabled: false,
    });
    render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Compare my strategies" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const details = (await screen.findByText("Stored evidence")).closest(
      "details",
    )!;
    expect(details).toHaveTextContent(
      "Full governed evidence beyond the reply budget.",
    );
    expect(details).toHaveTextContent(
      "Full additional explanation after the clean display ending.",
    );
    expect(details).toHaveTextContent("TradePlan: plan-ref");
    expect(details).not.toHaveAttribute("open");
  });

  it.each(["journal", "strategy", "knowledge", "Watcher", "trading", "risk"])(
    "uses one composer for voice about %s",
    async (topic) => {
      const text = `Review my ${topic}`;
      const provider = enableVoice(text);
      render(<AgentWorkspace />);
      fireEvent.click(screen.getByRole("button", { name: "History" }));
      fireEvent.click(await screen.findByRole("button", { name: "BTC plan" }));
      await screen.findAllByTestId("agent-message");
      const record = screen.getByRole("button", { name: "Record" });
      await waitFor(() => expect(record).toBeEnabled());
      await act(async () => fireEvent.click(record));
      const transcript = await screen.findByLabelText("Voice transcript");
      expect(provider.listen).toHaveBeenCalledOnce();
      fireEvent.change(transcript, {
        target: { value: text + " carefully" },
      });
      fireEvent.click(screen.getByRole("button", { name: "Append to draft" }));
      expect(apiMocks.agentTurn).not.toHaveBeenCalled();
      expect(screen.getByLabelText("Message")).toHaveValue(text + " carefully");
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
      await waitFor(() =>
        expect(apiMocks.agentTurn).toHaveBeenCalledExactlyOnceWith(
          {
            message: text + " carefully",
            conversation_id: "11111111-1111-4111-8111-111111111111",
            source_document_id: undefined,
          },
          { signal: expect.any(AbortSignal), headers: { "Idempotency-Key": expect.any(String) } },
        ),
      );
      expect(apiMocks.confirmProposal).not.toHaveBeenCalled();
    },
  );

  it("retains a failed message in the unified composer and displays a completed reply when history fails", async () => {
    enableVoice("Review risk before trading");
    apiMocks.agentTurn.mockRejectedValueOnce(new Error("Turn unavailable"));
    apiMocks.listMessages.mockRejectedValue(new Error("History unavailable"));
    render(<AgentWorkspace />);
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Record" })));
    fireEvent.click(screen.getByRole("button", { name: "Append to draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Turn unavailable",
    );
    expect(screen.getByLabelText("Message")).toHaveValue(
      "Review risk before trading",
    );
    fireEvent.click(screen.getByRole("button", { name: "Recover original request" }));
    await screen.findAllByTestId("agent-message");
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(2);
    expect(apiMocks.agentTurn.mock.calls[1][0]).toEqual(apiMocks.agentTurn.mock.calls[0][0]);
    expect(apiMocks.agentTurn.mock.calls[1][1].headers).toEqual(apiMocks.agentTurn.mock.calls[0][1].headers);
    expect(apiMocks.confirmProposal).not.toHaveBeenCalled();
  });

  it("recovers the original key and payload after a lost response and remount", async () => {
    apiMocks.agentTurn.mockRejectedValueOnce(new Error("Response lost"));
    const first = render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "My original intent" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/Response lost/);
    const original = apiMocks.agentTurn.mock.calls[0];
    first.unmount();
    render(<AgentWorkspace />);
    await screen.findByTestId("agent-turn-recovery");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Edited text stays separate" } });
    fireEvent.click(screen.getByRole("button", { name: "Recover original request" }));
    await waitFor(() => expect(apiMocks.agentTurn).toHaveBeenCalledTimes(2));
    expect(apiMocks.agentTurn.mock.calls[1][0]).toEqual(original[0]);
    expect(apiMocks.agentTurn.mock.calls[1][1].headers).toEqual(original[1].headers);
    expect(original[0].conversation_id).toBeUndefined();
  });

  it("lets locally rejected input be corrected and submitted through the generated client", async () => {
    apiMocks.agentTurn.mockImplementation(agentTurn);
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(agentTurnFixture)));
    vi.stubGlobal("fetch", fetchMock);
    render(<AgentWorkspace />);
    const invalid = "x".repeat(8001);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: invalid } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/Invalid API request/);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(pendingFor(recoveryScope, null)).toBeNull();
    expect(screen.queryByTestId("agent-turn-recovery")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Message")).toHaveValue(invalid);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Corrected intent" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(agentTurnFixture.reply);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ message: "Corrected intent" });
    expect(screen.queryByTestId("agent-turn-recovery")).not.toBeInTheDocument();
  });

  it("retains the attachment preview and imported reference after local rejection", async () => {
    apiMocks.agentTurn.mockImplementation(agentTurn);
    const documentId = "44444444-4444-4444-8444-444444444444";
    apiMocks.previewFile.mockResolvedValue({ title: "rules", extracted_text: "My rules", warnings: [], preview_receipt: "receipt" });
    apiMocks.importFile.mockResolvedValue({ document_id: documentId });
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(agentTurnFixture)));
    vi.stubGlobal("fetch", fetchMock);
    render(<AgentWorkspace />);
    const file = new File(["My rules"], "rules.txt", { type: "text/plain" });
    fireEvent.click(screen.getByRole("button", { name: "Attach document" }));
    fireEvent.change(screen.getByLabelText("Attach document"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "Preview attachment" }));
    await screen.findByLabelText("Attachment preview");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "x".repeat(8001) } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/Invalid API request/);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Attachment preview")).toHaveValue("My rules");
    expect(screen.queryByTestId("agent-turn-recovery")).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Use my rules" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(agentTurnFixture.reply);
    expect(apiMocks.importFile).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ message: "Use my rules", source_document_id: documentId });
  });

  it("retains a timed-out generated request across remount and only replays it on explicit recovery", async () => {
    apiMocks.agentTurn.mockImplementation(agentTurn);
    const fetchMock = vi.fn().mockImplementationOnce((_url, options: RequestInit) => new Promise((_resolve, reject) => {
      options.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    })).mockResolvedValue(new Response(JSON.stringify(agentTurnFixture)));
    vi.stubGlobal("fetch", fetchMock);
    const first = render(<AgentWorkspace />);
    await waitFor(() => expect(apiMocks.listConversations).toHaveBeenCalled());
    vi.useFakeTimers();
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Original intent" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await act(async () => vi.advanceTimersByTimeAsync(AGENT_TURN_TIMEOUT_MS));
    expect(screen.getByRole("alert")).toHaveTextContent("recover its saved result");
    const original = fetchMock.mock.calls[0][1];
    const pending = pendingFor(recoveryScope, null)!;
    expect(pending.body).toEqual(JSON.parse(original.body));
    expect(pending.key).toBe(original.headers["Idempotency-Key"]);
    await act(async () => vi.advanceTimersByTimeAsync(AGENT_TURN_TIMEOUT_MS));
    expect(fetchMock).toHaveBeenCalledOnce();
    vi.useRealTimers();
    first.unmount();
    render(<AgentWorkspace />);
    await screen.findByTestId("agent-turn-recovery");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "A separate edited draft" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Recover original request" }));
    await screen.findByText(agentTurnFixture.reply);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].body).toBe(original.body);
    expect(fetchMock.mock.calls[1][1].headers["Idempotency-Key"]).toBe(pending.key);
    expect(screen.getByLabelText("Message")).toHaveValue("A separate edited draft");
    expect(pendingFor(recoveryScope, agentTurnFixture.conversation_id)).toBeNull();
  });

  it.each([
    ["a contract-invalid body", JSON.stringify({ reply: "Possibly completed" }), /API response did not match its contract/],
    ["invalid JSON", "{", /API returned an invalid response/],
  ])("retains recovery after a successful HTTP response with %s", async (_label, body, error) => {
    apiMocks.agentTurn.mockImplementation(agentTurn);
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(body, { status: 200 }))
      .mockResolvedValue(new Response(JSON.stringify(agentTurnFixture)));
    vi.stubGlobal("fetch", fetchMock);
    render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Original intent" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(error);
    expect(screen.getByTestId("agent-turn-recovery")).toBeVisible();
    const original = fetchMock.mock.calls[0][1];
    const pending = pendingFor(recoveryScope, null)!;
    expect(pending.body).toEqual(JSON.parse(original.body));
    expect(pending.key).toBe(original.headers["Idempotency-Key"]);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Edited intent" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(fetchMock).toHaveBeenCalledOnce();
    fireEvent.click(screen.getByRole("button", { name: "Recover original request" }));
    await screen.findByText(agentTurnFixture.reply);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls[1][1].body).toBe(original.body);
    expect(fetchMock.mock.calls[1][1].headers["Idempotency-Key"]).toBe(pending.key);
    expect(screen.queryByTestId("agent-turn-recovery")).not.toBeInTheDocument();
  });

  it("allows ordinary chat and voice while execution is globally paused", async () => {
    const provider = enableVoice("My rule: wait for confirmation");
    apiMocks.killSwitchActive = true;
    render(<AgentWorkspace />);
    expect(
      screen.getByRole("button", { name: "Record" }),
    ).toBeEnabled();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Record" })));
    fireEvent.click(screen.getByRole("button", { name: "Append to draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(apiMocks.agentTurn).toHaveBeenCalledOnce());
    expect(provider.listen).toHaveBeenCalledOnce();
    expect(apiMocks.confirmProposal).not.toHaveBeenCalled();
  });

  it("bounds a stalled Agent turn, retains the transcript, and does not retry automatically", async () => {
    enableVoice("Review my journal");
    apiMocks.agentTurn.mockImplementationOnce(
      (_body, { signal }: { signal: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    render(<AgentWorkspace />);
    await waitFor(() => expect(apiMocks.listConversations).toHaveBeenCalled());
    vi.useFakeTimers();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Record" })));
    fireEvent.click(screen.getByRole("button", { name: "Append to draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await act(async () => vi.advanceTimersByTimeAsync(AGENT_TURN_TIMEOUT_MS));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "recover its saved result",
    );
    expect(screen.getByLabelText("Message")).toHaveValue("Review my journal");
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(1);
    expect(apiMocks.confirmProposal).not.toHaveBeenCalled();
  });

  it("shows a completed turn even when its history reload stalls", async () => {
    enableVoice("Review risk before trading");
    apiMocks.listMessages.mockImplementationOnce(
      (_id, _params, { signal }: { signal: AbortSignal }) =>
        new Promise((_resolve, reject) => {
          signal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    );
    render(<AgentWorkspace />);
    await waitFor(() => expect(apiMocks.listConversations).toHaveBeenCalled());
    vi.useFakeTimers();
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Record" })));
    fireEvent.click(screen.getByRole("button", { name: "Append to draft" }));
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await act(async () => vi.advanceTimersByTimeAsync(AGENT_TURN_TIMEOUT_MS));
    expect(screen.getByLabelText("Message")).toHaveValue("");
    expect(screen.getAllByTestId("agent-message")[1]).toHaveTextContent(
      "Stored evidence",
    );
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(1);
    expect(apiMocks.confirmProposal).not.toHaveBeenCalled();
  });

  it("previews an attachment before sending and passes only its persisted document reference", async () => {
    apiMocks.agentTurn.mockResolvedValue({
      ...agentTurnFixture, user_message_id: "m1", assistant_message_id: "m2",
      conversation_id: "11111111-1111-4111-8111-111111111111", capture_status: "failed", saved_entries: [],
      capture_source_message_id: "m1", capture_error: "Capture failed; reply saved.",
    });
    apiMocks.previewFile.mockResolvedValue({
      title: "rules",
      extracted_text: "Risk remains governed.",
      warnings: [],
      preview_receipt: "preview-receipt",
    });
    apiMocks.importFile.mockResolvedValue({ document_id: "44444444-4444-4444-8444-444444444444" });
    render(<AgentWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: "Attach document" }));
    const file = new File(["Risk remains governed."], "rules.txt", {
      type: "text/plain",
    });
    fireEvent.change(screen.getByLabelText("Attach document"), {
      target: { files: [file] },
    });
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Use my rules" },
    });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "Preview attachment" }));
    expect(await screen.findByLabelText("Attachment preview")).toHaveValue(
      "Risk remains governed.",
    );
    expect(apiMocks.importFile).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(apiMocks.agentTurn).toHaveBeenCalledWith(
        {
          message: "Use my rules",
          conversation_id: undefined,
          source_document_id: "44444444-4444-4444-8444-444444444444",
        },
        { signal: expect.any(AbortSignal), headers: { "Idempotency-Key": expect.any(String) } },
      ),
    );
    expect(apiMocks.importFile).toHaveBeenCalledWith(
      file,
      "rules",
      "general_note",
      "preview-receipt",
    );
    expect(apiMocks.confirmProposal).not.toHaveBeenCalled();
    expect(await screen.findByText(/Source document retained in this conversation/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Open original document" })).toHaveAttribute(
      "href", "/journal?tab=knowledge&document_id=44444444-4444-4444-8444-444444444444",
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Capture failed");
  });

  it("keeps the complete explanation and technical source references inside Stored evidence", async () => {
    apiMocks.listMessages.mockResolvedValue({
      items: [
        {
          id: "a",
          role: "assistant",
          created_at: "2026-01-01",
          content: "BTC short: entry 84,714.1. Missing risk narrative.",
          payload: {
            interactive_agent: {
              full_reply:
                "Complete additional explanation with 9c8c5c4f-3a8c-5301-bb63-b6b6a9bdc1b2.",
              recorded_evidence: "Canonical amounts and hash: abc123",
              sources: [{ title: "TradePlan", record_id: "plan-ref" }],
            },
          },
        },
      ],
    });
    render(<AgentWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: "BTC plan" }));
    const details = (await screen.findByText("Stored evidence")).closest(
      "details",
    )!;
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("Complete additional explanation");
    expect(details).toHaveTextContent("TradePlan: plan-ref");
    expect(details).toHaveTextContent("Canonical amounts and hash");
    expect(screen.getByText(/BTC short: entry/).closest("details")).toBeNull();
    expect(screen.getByText(/BTC short: entry/)).toBeVisible();
  });

  it("has collapsed history, no permanent capability panel or standalone trade context", async () => {
    render(<AgentWorkspace />);
    expect(
      screen.queryByRole("button", { name: "BTC plan" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByTestId("agent-capability-boundary"),
    ).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Timeframe")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Attach document" }));
    expect(screen.getByLabelText("Attach document")).toHaveAttribute(
      "type",
      "file",
    );
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: "BTC plan" }));
    const messages = await screen.findAllByTestId("agent-message");
    expect(messages[0]).toHaveAttribute("data-role", "user");
    expect(messages[1]).toHaveAttribute("data-role", "assistant");
    expect(screen.getByRole("button", { name: "History" })).toHaveAttribute(
      "aria-expanded",
      "false",
    );
  });

  it("confirms a proposal only from the explicit button", async () => {
    apiMocks.agentTurn.mockResolvedValue({
      user_message_id: "m1", assistant_message_id: "m2",
      conversation_id: "11111111-1111-4111-8111-111111111111",
      reply: "Drafted a journal proposal.",
      capability: "journal_capture",
      operation: "propose",
      proposals: [
        {
          proposal_id: "p1",
          conversation_id: "11111111-1111-4111-8111-111111111111",
          kind: "propose_journal_entry",
          artifact_kind: "journal_entry",
          status: "proposed",
          summary: "Journal draft",
          content_hash: "a".repeat(64),
          applied: false,
          authority_mutated: false,
        },
      ],
      limitations: [],
      authority_mutated: false,
      execution_attempted: false,
      real_trading_enabled: false,
    });
    render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Journal this trade." },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByTestId("agent-proposals")).toHaveTextContent(
      "proposed",
    );
    expect(apiMocks.confirmProposal).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm proposal" }));
    await waitFor(() =>
      expect(apiMocks.confirmProposal).toHaveBeenCalledTimes(1),
    );
    expect(apiMocks.confirmProposal).toHaveBeenCalledWith("p1", {
      conversation_id: "11111111-1111-4111-8111-111111111111",
      expected_content_hash: "a".repeat(64),
      statement: "I confirm",
    }, { signal: expect.any(AbortSignal) });
    await waitFor(() => expect(screen.getByTestId("agent-proposals")).toHaveTextContent("applied"));
  });

  it("clears the previous conversation while loading another and preserves failed message drafts", async () => {
    apiMocks.listConversations.mockResolvedValue({
      items: [
        { id: "11111111-1111-4111-8111-111111111111", title: "BTC plan" },
        { id: "c2", title: "ETH review" },
      ],
    });
    render(<AgentWorkspace />);
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(await screen.findByRole("button", { name: "BTC plan" }));
    await screen.findAllByTestId("agent-message");
    apiMocks.listMessages.mockRejectedValue(new Error("History offline"));
    fireEvent.click(screen.getByRole("button", { name: "History" }));
    fireEvent.click(screen.getByRole("button", { name: "ETH review" }));
    expect(screen.queryByText("Is this a pullback?")).not.toBeInTheDocument();
    expect(
      await screen.findByText(/Conversation unavailable: History offline/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("What are you working through?"),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "New conversation" }));
    apiMocks.agentTurn.mockRejectedValue(new Error("Agent offline"));
    fireEvent.change(screen.getByLabelText("Message"), {
      target: { value: "Review my risk" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Agent offline");
    expect(screen.getByLabelText("Message")).toHaveValue("Review my risk");
  });
  it.each(["empty", "incomplete", "same IDs", "failed"])("renders acknowledgment immediately and retains it through %s history", async (mode) => {
    let finish!: (value: unknown) => void;
    let fail!: (error: Error) => void;
    apiMocks.listMessages.mockImplementationOnce(() => new Promise((resolve, reject) => { finish = resolve; fail = reject; }));
    render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Stable turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findAllByTestId("agent-message");
    expect(screen.getByLabelText("Message")).toHaveValue("");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Next intentional turn" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeEnabled();
    expect(screen.getAllByTestId("agent-message").map(m => m.dataset.messageId)).toEqual(["m1", "m2"]);
    expect(screen.getAllByTestId("agent-message")[1]).toHaveTextContent("Noted.");
    await act(async () => {
      if (mode === "failed") fail(new Error("Refresh offline"));
      else finish({ items: mode === "empty" ? [] : mode === "incomplete" ? [{ id: "m1", role: "user", content: "Stable turn", created_at: "2026-10-09" }] : [
        { id: "m1", role: "user", content: "Stable turn", created_at: "2026-10-09" },
        { id: "m2", role: "assistant", content: "Noted.", created_at: "2026-10-09" },
      ] });
    });
    expect(screen.getAllByTestId("agent-message")).toHaveLength(2);
    expect(screen.getAllByTestId("agent-message")[1]).toHaveTextContent("Stored evidence");
    expect(apiMocks.agentTurn).toHaveBeenCalledTimes(1);
  });

  it("ignores an acknowledged turn arriving after conversation navigation", async () => {
    let finish!: (value: unknown) => void;
    apiMocks.agentTurn.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const { rerender } = render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Old context" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    testParams = new URLSearchParams("conversation=other");
    apiMocks.listMessages.mockResolvedValue({ items: [{ id: "other-message", role: "assistant", content: "Other conversation", created_at: "2026-10-09" }] });
    rerender(<AgentWorkspace />);
    await screen.findByText("Other conversation");
    await act(async () => finish({ conversation_id: "11111111-1111-4111-8111-111111111111", user_message_id: "m1", assistant_message_id: "m2", reply: "Late private response", proposals: [] }));
    expect(screen.queryByText("Late private response")).not.toBeInTheDocument();
    expect(screen.queryByText("Old context")).not.toBeInTheDocument();
  });

  it("ignores history arriving out of order after switching conversation", async () => {
    let finish!: (value: unknown) => void;
    apiMocks.listMessages.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    testParams = new URLSearchParams("conversation=first");
    const { rerender } = render(<AgentWorkspace />);
    testParams = new URLSearchParams("conversation=second");
    apiMocks.listMessages.mockResolvedValue({ items: [{ id: "second-message", role: "assistant", content: "Second history", created_at: "2026-10-09" }] });
    rerender(<AgentWorkspace />);
    await screen.findByText("Second history");
    await act(async () => finish({ items: [{ id: "first-message", role: "assistant", content: "Late first history", created_at: "2026-10-09" }] }));
    expect(screen.queryByText("Late first history")).not.toBeInTheDocument();
  });

  it("authorizes strategy context before sending and performs no navigation mutation", async () => {
    testParams = new URLSearchParams("strategy_id=55555555-5555-4555-8555-555555555555");
    apiMocks.getStrategy.mockResolvedValue({ id: "55555555-5555-4555-8555-555555555555", name: "Owned plan" });
    render(<AgentWorkspace />);
    await screen.findByText("Strategy: Owned plan");
    expect(apiMocks.agentTurn).not.toHaveBeenCalled();
    expect(screen.getByRole("link", { name: "Back to strategy" })).toHaveAttribute("href", "/strategy-lab/55555555-5555-4555-8555-555555555555");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Discuss changes" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(apiMocks.agentTurn).toHaveBeenCalledWith(expect.objectContaining({ strategy_id: "55555555-5555-4555-8555-555555555555", message: "Discuss changes" }), expect.any(Object)));
  });

  it("blocks sending when strategy access fails", async () => {
    testParams = new URLSearchParams("strategy_id=unauthorized");
    apiMocks.getStrategy.mockRejectedValue(new Error("Not found"));
    render(<AgentWorkspace />);
    await screen.findByText("Strategy unavailable: Not found");
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Discuss changes" } });
    expect(screen.getByRole("button", { name: "Send" })).toBeDisabled();
    expect(apiMocks.agentTurn).not.toHaveBeenCalled();
  });

  it("aborts a turn on logout and discards its late acknowledgment", async () => {
    const { sessionCleared } = await import("@/lib/auth/session-events");
    let finish!: (value: unknown) => void;
    apiMocks.agentTurn.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Private pending turn" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const signal = apiMocks.agentTurn.mock.calls[0][1].signal;
    act(() => sessionCleared());
    expect(signal.aborted).toBe(true);
    await act(async () => finish({ conversation_id: "old", user_message_id: "u", assistant_message_id: "a", reply: "Late private acknowledgment" }));
    expect(screen.queryByText("Late private acknowledgment")).not.toBeInTheDocument();
    expect(screen.queryByText("Private pending turn")).not.toBeInTheDocument();
  });

  it("a pending proposal decision cannot alter a newly selected conversation", async () => {
    let finish!: (value: unknown) => void;
    apiMocks.agentTurn.mockResolvedValue({ conversation_id: "11111111-1111-4111-8111-111111111111", user_message_id: "u", assistant_message_id: "a", reply: "Draft proposal",
      proposals: [{ proposal_id: "p1", conversation_id: "11111111-1111-4111-8111-111111111111", summary: "Draft journal", status: "proposed", kind: "propose_journal_entry" }] });
    apiMocks.confirmProposal.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const { rerender } = render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Draft" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm proposal" }));
    testParams = new URLSearchParams("conversation=other");
    apiMocks.listMessages.mockResolvedValue({ items: [] });
    rerender(<AgentWorkspace />);
    await act(async () => finish({ proposal_id: "p1", conversation_id: "11111111-1111-4111-8111-111111111111", status: "applied" }));
    expect(screen.queryByTestId("agent-proposals")).not.toBeInTheDocument();
  });

  it("cancels a pending turn on unmount and starts no history reconciliation for a late reply", async () => {
    let finish!: (value: unknown) => void;
    apiMocks.agentTurn.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    const { unmount } = render(<AgentWorkspace />);
    fireEvent.change(screen.getByLabelText("Message"), { target: { value: "Pending" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    const signal = apiMocks.agentTurn.mock.calls[0][1].signal;
    unmount();
    expect(signal.aborted).toBe(true);
    await act(async () => finish({ conversation_id: "old", user_message_id: "u", assistant_message_id: "a", reply: "Late" }));
    expect(apiMocks.listMessages).not.toHaveBeenCalled();
  });

});
