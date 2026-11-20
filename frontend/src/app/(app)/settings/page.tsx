"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";
import { SessionScoped } from "@/components/settings/SessionScoped";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { PageHeader } from "@/components/ui/page-header";
import { Row } from "@/components/ui/row";
import { useAuth } from "@/contexts/AuthContext";
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
  const { user, organization, logout } = useAuth();
  useHashForward();
  return (
    <div className="page-sections" data-testid="settings-workspace">
      <PageHeader title="Account" />
      <Card>
        <CardContent className="divide-y divide-border-subtle">
          <Row label="Email" value={user?.email || "Unavailable"} />
          <Row label="Workspace" value={organization?.name || "Unavailable"} />
        </CardContent>
      </Card>
      <div>
        <Button variant="outline" className="h-11" onClick={() => void logout()}>
          Log out
        </Button>
      </div>
    </div>
  );
}
