"use client";
import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
export function journalReturnKey(userId?: string, organizationId?: string) {
  return `alphatrade.journal.return:${organizationId}:${userId}`;
}
export function JournalReturnLink() {
  const { user, organization } = useAuth();
  const pathname = usePathname();
  const params = useSearchParams();
  const fallback = pathname === "/knowledge" || params.get("tab") === "knowledge"
    ? "/journal?tab=knowledge" : "/journal";
  const [href, setHref] = useState(fallback);
  useEffect(() => {
    setHref(fallback);
    try {
      const stored = JSON.parse(
        sessionStorage.getItem(journalReturnKey(user?.id, organization?.id)) ??
          "null",
      );
      if (stored?.href?.match(/^\/(journal|knowledge)(\?|$)/))
        setHref(stored.href);
    } catch {
      /* direct link */
    }
  }, [user?.id, organization?.id, fallback]);
  return (
    <Link
      href={href}
      className="inline-flex min-h-11 items-center text-sm text-accent"
    >
      {href.startsWith("/knowledge") || href.includes("tab=knowledge")
        ? "← Back to Knowledge" : "← Back to Journal"}
    </Link>
  );
}
