"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { EmailVerificationNotice } from "@/components/account/EmailVerificationNotice";
import { PaperAccountSetup } from "@/components/settings/PaperAccountSetup";
import { SessionScoped } from "@/components/settings/SessionScoped";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Row } from "@/components/ui/row";
import { useAuth } from "@/contexts/AuthContext";
import { useAppContext } from "@/contexts/AppContext";
import { SETTINGS_HASH_FORWARDS } from "@/lib/navigation/settings-sections";

export default function SettingsPage() {
  return (
    <SessionScoped>
      <AccountSettings />
    </SessionScoped>
  );
}

/** Old `/settings#notifications` and `#markets` links open their own section. */
function useHashForward() {
  const router = useRouter();
  useEffect(() => {
    const forward = () => {
      const target = SETTINGS_HASH_FORWARDS[window.location.hash.slice(1)];
      if (target) router.replace(target);
    };
    forward();
    window.addEventListener("hashchange", forward);
    return () => window.removeEventListener("hashchange", forward);
  }, [router]);
}

function AccountSettings() {
  const { user, organization } = useAuth();
  const { health, providers, refreshStatus, loading } = useAppContext();
  useHashForward();
  const openLink = (href: string, label: string) => (
    <Link className="inline-flex h-10 items-center text-sm text-accent" href={href} aria-label={label}>
      Open
    </Link>
  );
  return (
    <div className="page-sections [&_input:not([type=checkbox])]:text-base [&_select]:text-base lg:[&_input:not([type=checkbox])]:text-sm lg:[&_select]:text-sm" data-testid="settings-workspace">
      <PageHeader title="Account" />
      <Card>
        <CardContent className="divide-y divide-border-subtle">
          <Row label="Email" value={user?.email ?? "Unavailable"} />
          <Row label="Workspace" value={organization?.name ?? "Unavailable"} />
          <Row label="Team" value="Members and invitations" action={openLink("/settings/team", "Team")} />
          <Row label="Billing & usage" value="Plan and usage" action={openLink("/settings/billing", "Billing & usage")} />
        </CardContent>
      </Card>
      <EmailVerificationNotice />
      <PaperAccountSetup key={`${organization?.id}:${user?.id}`} />
      <details className="rounded-card border border-border-subtle bg-surface-1 p-4" id="account-system">
        <summary className="min-h-11 cursor-pointer text-sm">System</summary>
        <div className="space-y-2 pt-2">
          <Row label="API" value={health?.status ?? "Unavailable"} />
          {providers?.providers.map((p) => (
            <Row
              key={`${p.kind}:${p.name}`}
              label={p.name}
              value={`${p.health}${p.is_mock ? " · Simulated" : ""}${p.using_fallback ? " · Fallback" : ""}`}
            />
          )) ?? <p className="text-sm">Unavailable</p>}
          <Button variant="outline" className="h-11" disabled={loading} onClick={() => void refreshStatus()}>
            Refresh system status
          </Button>
        </div>
      </details>
    </div>
  );
}
