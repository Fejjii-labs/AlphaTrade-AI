"use client";

import { useSearchParams } from "next/navigation";

import { AgentWorkspace } from "@/components/agent/AgentWorkspace";
import { DocumentsPanel } from "@/components/agent/DocumentsPanel";
import { LinkTabs } from "@/components/ui/link-tabs";
import { PageHeader } from "@/components/ui/page-header";

function AgentTabs({ active }: { active: "chat" | "documents" }) {
  return (
    <LinkTabs
      aria-label="Agent views"
      activeId={active}
      items={[
        { id: "chat", label: "Chat", href: "/agent" },
        { id: "documents", label: "Documents", href: "/agent?tab=documents" },
      ]}
    />
  );
}

export default function AgentPage() {
  const params = useSearchParams();
  if (params.get("tab") === "documents")
    return (
      <div className="page-sections" data-testid="agent-documents">
        <PageHeader title="Agent" />
        <AgentTabs active="documents" />
        <DocumentsPanel documentId={params.get("document_id") ?? params.get("document")} savedId={params.get("saved")} />
      </div>
    );
  return <AgentWorkspace tabs={<AgentTabs active="chat" />} />;
}
