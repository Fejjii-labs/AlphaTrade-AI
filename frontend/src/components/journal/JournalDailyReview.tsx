"use client";

import { DailyReviewCard } from "@/components/dashboard/DailyReviewCard";
import { useResolvedTimezone } from "@/hooks/useResolvedTimezone";

/** Daily Review on the Trades page, bucketed in the shared day zone. */
export function JournalDailyReview() {
  const zone = useResolvedTimezone();
  return <DailyReviewCard compact timezone={zone.ready ? zone.timezone : null} />;
}
