"use client";

import { useAuth } from "@/contexts/AuthContext";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect } from "react";

import { knowledgeRedirect, parseJournalQuery } from "@/components/journal/journalContext";
import { JournalReturnLink } from "@/components/journal/JournalReturnLink";
import { JournalTradeScreen } from "@/components/journal/JournalTradeScreen";
import { TradesScreen } from "@/components/journal/TradesScreen";

export default function JournalPage() {
  const { user, organization } = useAuth();
  const searchParams = useSearchParams();
  const router = useRouter();
  const context = parseJournalQuery(searchParams);
  const redirect = knowledgeRedirect(new URLSearchParams(searchParams));
  useEffect(() => {
    if (redirect) router.replace(redirect);
  }, [redirect, router]);
  if (redirect) return null;
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
  return <TradesScreen />;
}
