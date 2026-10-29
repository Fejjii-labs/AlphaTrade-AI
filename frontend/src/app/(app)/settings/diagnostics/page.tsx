"use client";

import Link from "next/link";

import { SafetyDisclaimers } from "@/components/SafetyDisclaimers";
import { Button } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { Row } from "@/components/ui/row";
import { StatusTile } from "@/components/ui/status-tile";
import { useAppContext } from "@/contexts/AppContext";
import { appConfig } from "@/lib/config";
import { DIAGNOSTICS_GROUPS, routesOfKind } from "@/lib/navigation/route-registry";
import { ProviderFreshness } from "@/components/ProviderFreshness";
import { apiTile, providerTiles } from "@/lib/status-tiles";

/** Settings → Diagnostics: provider status tiles and every out-of-nav tool page. */
export default function DiagnosticsPage() {
  const { health, providers, refreshStatus, loading } = useAppContext();
  const tiles = [apiTile(health), ...providerTiles(providers?.providers ?? [])];
  const routes = routesOfKind("diagnostics");
  return (
    <div className="page-sections" data-testid="settings-diagnostics">
      <PageHeader
        title="Diagnostics"
        actions={
          <Button variant="outline" className="h-11" disabled={loading} onClick={() => void refreshStatus()}>
            Refresh status
          </Button>
        }
      />
      <section aria-labelledby="diagnostics-providers" className="space-y-2">
        <h2 id="diagnostics-providers" className="text-section">Providers</h2>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
          {tiles.map((tile) => (
            <StatusTile key={tile.name} {...tile} />
          ))}
        </div>
        {providers ? <ProviderFreshness providers={providers} /> : (
          <p className="text-sm text-text-muted">Provider status unavailable.</p>
        )}
      </section>
      <section aria-labelledby="diagnostics-tools" className="space-y-1">
        <h2 id="diagnostics-tools" className="text-section">Tools</h2>
        <div className="divide-y divide-border-subtle">
          {DIAGNOSTICS_GROUPS.map((group) => (
            <Row
              key={group}
              label={group}
              value={
                <ul className="flex flex-wrap gap-x-3 gap-y-1">
                  {routes
                    .filter((route) => route.group === group)
                    .map((route) => (
                      <li key={route.path}>
                        <Link className="text-accent hover:underline" href={route.path}>
                          {route.label}
                        </Link>
                      </li>
                    ))}
                </ul>
              }
            />
          ))}
        </div>
      </section>
      <details className="rounded-card border border-border-subtle bg-surface-1 p-4" data-testid="settings-build-config">
        <summary className="cursor-pointer text-sm">Build configuration</summary>
        <div className="pt-2">
          <Row label="API URL" value={appConfig.apiBaseUrl} />
          <Row label="Execution mode" value={appConfig.executionMode} />
          <Row label="Provider mode" value={appConfig.providerMode} />
        </div>
      </details>
      <details className="rounded-card border border-border-subtle bg-surface-1 p-4">
        <summary className="cursor-pointer text-sm">Safety &amp; disclaimers</summary>
        <div className="pt-2">
          <SafetyDisclaimers />
        </div>
      </details>
    </div>
  );
}
