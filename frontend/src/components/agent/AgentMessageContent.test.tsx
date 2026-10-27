import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { AgentMessageContent } from "./AgentMessageContent";
import { expectNoIdentifiers } from "@/test/no-identifiers";
afterEach(cleanup);
it("renders one concise response and keeps raw evidence collapsed without a duplicate explanation", () => {
  render(<AgentMessageContent message={{ role: "assistant", content: "BTCUSDT long · BloFin demo. Entry verified; exit unverified.", payload: { interactive_agent: { full_reply: "Duplicate long explanation", recorded_evidence: "command_id=exact-command; historical fill only" } } }} />);
  expect(screen.getByText("BTCUSDT long · BloFin demo. Entry verified; exit unverified.")).toBeVisible();
  const details = screen.getByText("Sources").closest("details");
  expect(details).not.toHaveAttribute("open");
  expect(screen.getByText(/command_id=exact-command/)).not.toBeVisible();
  expect(screen.getByText(/Duplicate long explanation/)).not.toBeVisible();
  expect(screen.queryByText(/Recorded facts/)).not.toBeInTheDocument();
  expect(screen.queryByText(/currently open/i)).not.toBeInTheDocument();
  expectNoIdentifiers();
});
it("shows document guidance as a muted caption under the reply", () => {
  const guidance =
    "Document guidance is reference data; these passages do not establish approved settings.";
  render(
    <AgentMessageContent
      message={{
        role: "assistant",
        content: `Yep, your message came through.\n\n${guidance}\n\nRecorded facts (not a confirmation):\nchunk=1`,
      }}
    />,
  );
  expect(screen.getByText("Yep, your message came through.")).toBeVisible();
  const caption = screen.getByText(guidance);
  expect(caption).toBeVisible();
  expect(caption).toHaveClass("text-xs", "text-text-muted");
  expect(caption.closest("details")).toBeNull();
  const details = screen.getByText("Sources").closest("details");
  expect(details).not.toHaveAttribute("open");
  expect(details).not.toHaveTextContent(guidance);
  expect(screen.getByText("chunk=1").closest("[data-technical-details]")).not.toBeNull();
});
it("moves historical appended facts into the collapsed evidence panel", () => {
  render(<AgentMessageContent message={{ role: "assistant", content: "Entry verified.\n\nRecorded facts (not a confirmation):\nRaw identity evidence" }} />);
  expect(screen.getByText("Entry verified.")).toBeVisible();
  expect(screen.getByText("Raw identity evidence")).not.toBeVisible();
});
const NOTE_A = "11111111-2222-4333-8444-555555555555";
const NOTE_B = "66666666-7777-4888-8999-aaaaaaaaaaaa";
const NOTE_C = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff";
const COMMAND = "12121212-3434-4565-8787-909090909090";
it("keeps raw provenance in collapsed Technical details and out of the main view", () => {
  render(
    <AgentMessageContent
      message={{
        role: "assistant",
        content: "Your saved risk note says to size down.",
        payload: { interactive_agent: { recorded_evidence: `command_id=${COMMAND}; venue=BLOFIN_DEMO`, sources: [{ artifact_kind: "lesson", record_id: NOTE_A, title: "Risk notes", relation: "saved user note", provenance: "user_supplied" }] } },
      }}
    />,
  );
  const technical = screen.getByText("Technical details").closest("details")!;
  expect(technical).not.toHaveAttribute("open");
  expect(technical).toHaveTextContent(NOTE_A);
  expect(technical).toHaveTextContent(`command_id=${COMMAND}`);
  expect(screen.getByText(/venue=BLOFIN_DEMO/)).not.toBeVisible();
  expect(technical.parentElement?.closest("details")).toBe(screen.getByText("Sources").closest("details"));
  expect(screen.getByRole("list", { name: "Source references" })).not.toHaveTextContent(NOTE_A);
  expect(screen.getByRole("link", { name: "Source: Risk notes" })).toHaveAttribute("href", `/journal?saved=${NOTE_A}`);
  expectNoIdentifiers();
});
it("gives sources with repeated or empty titles distinct readable names and link labels", () => {
  const note = (record_id: string, title: string) => ({ artifact_kind: "lesson", record_id, title, relation: "saved user note", provenance: "user_supplied" });
  render(
    <AgentMessageContent
      message={{
        role: "assistant",
        content: "Three notes matched.",
        payload: { interactive_agent: { sources: [note(NOTE_A, "Risk notes"), note(NOTE_B, "Risk notes"), note(NOTE_C, ""), { artifact_kind: "trade_decision", record_id: COMMAND, title: "  ", relation: "manual demo command", provenance: "system_generated" }] } },
      }}
    />,
  );
  const list = screen.getByRole("list", { name: "Source references" });
  const links = within(list).getAllByRole("link");
  expect(links.map((link) => link.getAttribute("aria-label"))).toEqual([
    "Source: Risk notes",
    "Source: Risk notes (2)",
    "Source: Source 3",
    "Source: Source 4",
  ]);
  expect(new Set(links.map((link) => link.getAttribute("aria-label"))).size).toBe(links.length);
  expect(links.map((link) => link.textContent)).toEqual(["Risk notes", "Risk notes (2)", "Source 3", "Source 4"]);
  expect(links[1]).toHaveAttribute("href", `/journal?saved=${NOTE_B}`);
  const technical = screen.getByText("Technical details").closest("details")!;
  expect(within(technical).getByText("Risk notes (2)").nextSibling).toHaveTextContent(NOTE_B);
  expectNoIdentifiers();
});
it("names repeated manual demo choices distinctly", () => {
  const choice = (record_id: string, title: string) => ({ artifact_kind: "trade_decision", record_id, title, relation: "manual demo choice", provenance: "system_generated" });
  render(
    <AgentMessageContent
      message={{ role: "assistant", content: "Two attempts matched.", payload: { interactive_agent: { sources: [choice(NOTE_A, "BTCUSDT attempt"), choice(NOTE_B, "BTCUSDT attempt")] } } }}
    />,
  );
  const choices = within(screen.getByRole("list", { name: "Matching manual demo attempts" })).getAllByRole("link");
  expect(choices.map((link) => link.textContent)).toEqual(["BTCUSDT attempt", "BTCUSDT attempt (2)"]);
  expect(choices[1]).toHaveAttribute("href", `/execution/manual-demo/${NOTE_B}`);
  expectNoIdentifiers();
});
