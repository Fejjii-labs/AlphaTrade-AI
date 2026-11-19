"use client";

import { useAuth } from "@/contexts/AuthContext";
import { useSearchParams } from "next/navigation";

import { JournalHubScreen } from "@/components/journal/JournalHubScreen";
import { parseJournalQuery } from "@/components/journal/journalContext";
import { JournalTradeScreen } from "@/components/journal/JournalTradeScreen";
import { TradesScreen } from "@/components/journal/TradesScreen";
import { JournalReturnLink } from "@/components/journal/JournalReturnLink";

export default function JournalPage() {
  const { user, organization } = useAuth();
  const searchParams = useSearchParams();
  const context = parseJournalQuery(searchParams);
  if (context.tradeId)
    return (
      <div className="space-y-3">
        <JournalReturnLink />
        <JournalTradeScreen
          key={`${organization?.id}:${user?.id}:${context.tradeId}`}
          tradeId={context.tradeId}
        />
      </div>
    );
  const record =
    searchParams.get("view") === "record" ||
    Boolean(
      context.proposalId ||
      context.positionId ||
      context.entryId ||
      context.tradeId ||
      context.sessionId,
    );
  if (record)
    return (
      <div className="space-y-3">
        <JournalReturnLink />
        <JournalHubScreen />
      </div>
    );
  return <TradesScreen />;
}
