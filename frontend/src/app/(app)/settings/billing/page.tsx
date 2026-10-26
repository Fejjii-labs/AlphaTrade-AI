"use client";

import { BillingPageView } from "@/components/billing/BillingPageView";
import { UsagePageView } from "@/components/usage/UsagePageView";
import { PageHeader } from "@/components/ui/page-header";

/**
 * Canonical Settings → Billing & Usage L2 section (AT-040 Phase B).
 * Preserves both billing and usage capabilities under one route.
 */
export default function SettingsBillingAndUsagePage() {
  return (
    <div className="space-y-10" data-testid="settings-billing-usage-page">
      <PageHeader
        title="Billing & Usage"
        description="Plans, billing, and token usage."
      />
      <BillingPageView embedded />
      <section
        id="usage"
        data-testid="billing-usage-section"
        className="space-y-4 border-t border-border-subtle pt-8"
      >
        <UsagePageView embedded omitQuota />
      </section>
    </div>
  );
}
