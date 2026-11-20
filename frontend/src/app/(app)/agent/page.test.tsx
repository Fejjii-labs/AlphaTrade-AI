import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import AgentPage from "./page";

const params = new URLSearchParams();
vi.mock("next/navigation", () => ({ useSearchParams: () => params }));
vi.mock("@/components/agent/AgentWorkspace", () => ({
  AgentWorkspace: ({ tabs }: { tabs?: React.ReactNode }) => <div>{tabs}<p>chat body</p></div>,
}));
vi.mock("@/components/agent/DocumentsPanel", () => ({
  DocumentsPanel: ({ documentId, savedId }: { documentId: string | null; savedId: string | null }) => (
    <p>documents body {documentId ?? "-"} {savedId ?? "-"}</p>
  ),
}));

afterEach(() => { cleanup(); for (const key of [...params.keys()]) params.delete(key); });

it("shows Chat by default with Chat / Documents tabs", () => {
  render(<AgentPage />);
  expect(screen.getByText("chat body")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Chat" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByRole("link", { name: "Documents" })).toHaveAttribute("href", "/agent?tab=documents");
});

it("keeps ?strategy_id= on the chat view", () => {
  params.set("strategy_id", "s1");
  render(<AgentPage />);
  expect(screen.getByText("chat body")).toBeInTheDocument();
});

it("opens Documents, passing document and saved deep links", () => {
  params.set("tab", "documents");
  params.set("document_id", "doc-1");
  render(<AgentPage />);
  expect(screen.getByText("documents body doc-1 -")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Documents" })).toHaveAttribute("aria-current", "page");
  expect(screen.queryByText("chat body")).not.toBeInTheDocument();
});
