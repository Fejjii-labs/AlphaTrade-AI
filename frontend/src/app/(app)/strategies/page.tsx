"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { FamilyCard } from "@/components/strategies/FamilyCard";
import { StrategyRules } from "@/components/strategies/StrategyRules";
import { buttonVariants } from "@/components/ui/button";
import { PageHeader } from "@/components/ui/page-header";
import { useAuth } from "@/contexts/AuthContext";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { BrainFamily, BrainOutcomes, BrainOverview, BrainSetup } from "@/lib/api/brain-types";
import { cn } from "@/lib/utils";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";

const families = [
  {
    id: "nested",
    name: "Nested Continuation",
    detectionFamily: "nested_continuation",
    kind: "operational_nested_continuation/v1",
  },
  { id: "sfp", name: "SFP", kind: "swing_failure_pattern/v1", detectionFamily: "sfp" },
  { id: "dline", name: "D-line", kind: "d_line/v1", detectionFamily: "d_line" },
] satisfies { id: string; name: string; kind: string; detectionFamily: BrainFamily }[];

type FamilyRead = { setups: BrainSetup[]; outcomes: BrainOutcomes | null };

function familyRead(view: BrainOverview): FamilyRead {
  return { setups: view.setups, outcomes: view.outcomes ?? null };
}

function sentence(token: string): string {
  const text = token.replaceAll("_", " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export default function StrategiesPage() {
  const { user, organization } = useAuth();
  const [session, setSession] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => setSession(sessionGeneration())), []);
  if (!user || !organization) return null;
  return (
    <StrategiesWorkspace key={JSON.stringify([organization.id, user.id, session])} />
  );
}

function StrategiesWorkspace() {
  const params = useSearchParams();
  const family = families.find((f) => f.id === params.get("family"));
  const loader = useCallback(async () => {
    // One bounded read per family, so a busy SFP week cannot push Nested setups out.
    const since = new Date(Date.now() - 7 * 86400000).toISOString();
    const [nested, sfp, dline] = await Promise.allSettled([
      api.strategyBrain.overview({ family: "nested_continuation", since }),
      api.strategyBrain.overview({ family: "sfp", since }),
      api.strategyBrain.overview({ family: "d_line", since }),
    ]);
    const brain = [nested, sfp, dline].find((r) => r.status === "fulfilled");
    return {
      brain: brain?.status === "fulfilled" ? brain.value : null,
      recent: {
        nested_continuation: nested.status === "fulfilled" ? familyRead(nested.value) : null,
        d_line: dline.status === "fulfilled" ? familyRead(dline.value) : null,
        sfp: sfp.status === "fulfilled" ? familyRead(sfp.value) : null,
      } as Record<BrainFamily, FamilyRead | null>,
    };
  }, []);
  const { data, loading } = useAsyncData(loader, []);
  const router = useRouter();
  useEffect(() => {
    // Risk policy moved to the Bot page; old `/strategies#risk` links follow it.
    if (window.location.hash === "#risk") router.replace("/autonomous#limits");
  }, [router]);
  return (
    <div className="page-sections" data-testid="trader-strategies">
      <PageHeader
        title={family?.name ?? "Strategies"}
        actions={
          <Link
            href="/agent?discuss=strategy"
            className={cn(
              buttonVariants({ variant: "outline" }),
              "h-11 border-accent-border bg-accent-muted text-accent hover:bg-accent-muted",
            )}
          >
            Discuss a strategy
          </Link>
        }
      />
      {family ? (
        <>
          <Link
            href="/strategies"
            className="inline-flex min-h-11 items-center text-accent"
          >
            ← Strategies
          </Link>
          {family.id === "dline" && <p>Draft research only. No execution or alerts are authorized.</p>}
          {data?.brain?.strategies
            .filter((s) => s.spec.kind === family.kind)
            .map((s) => (
              <article
                key={s.version_id}
                className="space-y-2 rounded-card border border-border-subtle p-4"
              >
                <div className="flex flex-wrap justify-between gap-2">
                  <h2 className="font-semibold">
                    {s.spec.symbol} · {sentence(s.spec.direction)} · {s.spec.trigger_timeframe}
                  </h2>
                  <span className="text-sm">
                    {s.enabled ? "On" : "Off"} · v{s.version}
                  </span>
                </div>
                <details>
                  <summary className="min-h-11 cursor-pointer text-sm">
                    Rules
                  </summary>
                  <StrategyRules spec={s.spec} />
                </details>
              </article>
            ))}
          {!loading && !data?.brain && (
            <p role="status">Strategies unavailable.</p>
          )}
        </>
      ) : (
        <div className="grid items-start gap-3 md:grid-cols-2">
          {families.map((f) => {
            const read = data?.recent[f.detectionFamily] ?? null;
            return (
              <FamilyCard
                key={f.id}
                id={f.id}
                name={f.name}
                loading={loading}
                strategies={data?.brain ? data.brain.strategies.filter((s) => s.spec.kind === f.kind) : null}
                outcomes={read?.outcomes ?? null}
                setups={read?.setups ?? null}
              />
            );
          })}
        </div>
      )}
    </div>
  );
}
