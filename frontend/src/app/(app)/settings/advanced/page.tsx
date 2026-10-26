import Link from "next/link";

import {
  ADVANCED_ROUTE_GROUPS,
  ADVANCED_ROUTES,
} from "@/components/layout/advanced-routes";
import { PageHeader } from "@/components/ui/page-header";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SafetyDisclaimers } from "@/components/SafetyDisclaimers";
import { appConfig } from "@/lib/config";

export default function AdvancedSettingsPage() {
  return (
    <div className="space-y-6" data-testid="settings-advanced-page">
      <PageHeader
        title="Advanced"
        description="Operational, audit, and engineering pages."
      />
      {ADVANCED_ROUTE_GROUPS.map((group) => {
        const routes = ADVANCED_ROUTES.filter((route) => route.group === group);
        if (routes.length === 0) return null;
        return (
          <section key={group} className="space-y-2">
            <h2 className="text-sm font-medium text-text-primary">{group}</h2>
            <ul className="grid gap-2 sm:grid-cols-2">
              {routes.map((route) => (
                <li key={route.href}>
                  <Link
                    href={route.href}
                    className="block rounded-card border border-border-subtle bg-surface-1 px-3 py-3 text-sm text-text-primary hover:bg-surface-2"
                  >
                    {route.label}
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
      <Card data-testid="settings-build-config">
        <CardHeader>
          <CardTitle>Build configuration (not runtime-verified)</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-2 break-words text-sm text-text-secondary sm:grid-cols-2">
          <span>API URL: {appConfig.apiBaseUrl}</span>
          <span>Execution mode (build config): {appConfig.executionMode}</span>
          <span>Provider mode (build config): {appConfig.providerMode}</span>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Safety &amp; disclaimers</CardTitle>
        </CardHeader>
        <CardContent>
          <SafetyDisclaimers />
        </CardContent>
      </Card>
    </div>
  );
}
