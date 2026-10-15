"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { Button } from "@/components/ui/button";
import { rejectedBeforeCommit } from "./request-recovery";
import { experimentCreate, paperAccount, strategyVersions } from "@/lib/api/generated/client";
import { experimentCreateRequest } from "@/lib/api/generated/validators";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import { recoverySessionId } from "@/lib/auth/recovery-session";

type Version = Awaited<ReturnType<typeof strategyVersions>>["items"][number];
type Body = Parameters<typeof experimentCreate>[0];
type Configuration = Body["configuration"];
const prefix = "alphatrade:experiment-draft:";
const moneyFields = [
  ["max_risk_per_trade", "Risk per trade"], ["max_position_notional", "Position notional"],
  ["max_total_exposure", "Total exposure"], ["max_daily_loss", "Daily loss"],
  ["max_weekly_loss", "Weekly loss"], ["max_drawdown", "Drawdown"], ["cost_allowance", "Cost allowance"],
] as const;
function saved(key: string): Body | null {
  try { const body = JSON.parse(sessionStorage.getItem(key) ?? "null"); return experimentCreateRequest(body) ? body : null; } catch { return null; }
}
function validBounds(risk: Configuration["risk_limits"], target: Configuration["sample_target"]) {
  const scaled = (value: unknown) => {
    if (typeof value !== "string" || !/^(?:0|[1-9]\d{0,11})(?:\.\d{1,12})?$/.test(value)) return null;
    const [whole, fraction = ""] = value.split(".");
    return BigInt(whole + fraction.padEnd(12, "0"));
  };
  const values = Object.fromEntries(moneyFields.map(([key]) => [key, scaled(risk[key])]));
  if (Object.values(values).some(value => value === null) || moneyFields.some(([key]) => key !== "cost_allowance" && values[key]! <= BigInt(0))) return false;
  const leverage = scaled(risk.max_leverage);
  return leverage !== null && leverage > BigInt(0) && leverage <= scaled("10")!
    && values.max_risk_per_trade! <= values.max_daily_loss! && values.max_daily_loss! <= values.max_weekly_loss!
    && values.max_position_notional! <= values.max_total_exposure! && values.cost_allowance! < values.max_risk_per_trade!
    && Number.isInteger(target.minimum) && Number.isInteger(target.maximum) && target.minimum <= target.maximum;
}
export function ExperimentDraftPanel({ strategyId, name }: { strategyId: string; name: string }) {
  const { user, organization } = useAuth();
  const [generation, setGeneration] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => {
    setGeneration(sessionGeneration());
  }), []);
  if (!user || !organization) return null;
  const session = recoverySessionId(organization.id, user.id);
  if (!session) return null;
  const scope = `${prefix}${JSON.stringify([organization.id, user.id, session, strategyId])}`;
  return <ScopedDraft key={`${scope}:${generation}`} scope={scope} strategyId={strategyId} name={name} />;
}
function ScopedDraft({ scope, strategyId, name }: { scope: string; strategyId: string; name: string }) {
  const [versions, setVersions] = useState<Version[]>([]);
  const [account, setAccount] = useState<Awaited<ReturnType<typeof paperAccount>>["account"]>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [versionId, setVersionId] = useState("");
  const [mode, setMode] = useState<Configuration["mode"]>("exploration");
  const [fields, setFields] = useState<Record<string, string>>({ minimum: "30", maximum: "100", max_leverage: "1", max_open_positions: "1", max_trades_per_day: "1", max_trades_total: "100" });
  const [title, setTitle] = useState(`${name.slice(0, 95)} research`);
  const [pending, setPending] = useState<Body | null>(() => saved(scope));
  const [created, setCreated] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    const abort = new AbortController();
    void Promise.all([strategyVersions(strategyId, { limit: 100 }, { signal: abort.signal }), paperAccount({ signal: abort.signal })]).then(([result, status]) => {
      if (abort.signal.aborted) return;
      const supported = result.items.filter(version => ["operational_nested_continuation/v1", "swing_failure_pattern/v1", "trendpulse_1r/v1"].includes(String(version.pattern_spec?.kind))).sort((a, b) => b.version - a.version);
      setVersions(supported); setVersionId(supported[0]?.id ?? ""); setAccount(status.account);
    }).catch(() => { if (!abort.signal.aborted) setError(true); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [strategyId]);
  function field(key: string, label: string, type = "text") {
    return <label className="text-sm" key={key}>{label}<input aria-label={label} type={type} required value={fields[key] ?? ""} onChange={event => setFields(old => ({ ...old, [key]: event.target.value }))} className="block min-h-11 w-full rounded-control bg-surface-0 p-2" /></label>;
  }
  async function create(original?: Body) {
    if (controller.current) return;
    const version = versions.find(item => item.id === versionId);
    const spec = version?.pattern_spec;
    if (!original && (!version || !spec || !account?.enabled)) return;
    const body: Body = original ?? {
      name: title, idempotency_key: crypto.randomUUID(), configuration: {
        mode, strategy_id: strategyId, strategy_version_id: version!.id,
        family: spec!.kind as Configuration["family"],
        account: { execution_account_id: account!.id, source: "internal_simulation", native_uid: null, execution_identity_audit_id: null },
        variants: [{ key: "baseline", strategy_version_id: version!.id, parameters: spec!.parameters as Record<string, unknown> }],
        symbols: [String(spec!.symbol)], timeframes: [...new Set([spec!.trigger_timeframe, ...(spec!.kind === "trendpulse_1r/v1" ? [spec!.trend_timeframe] : [])])] as Configuration["timeframes"],
        model_policy: { mode: "disabled", provider: null, model: null, max_calls: 0, max_tokens: 0, max_cost_usd: "0" },
        sample_target: { kind: "setup_observation", minimum: Number(fields.minimum), maximum: Number(fields.maximum) },
        risk_limits: {
          quote_currency: "USDT", max_risk_per_trade: fields.max_risk_per_trade, max_position_notional: fields.max_position_notional,
          max_total_exposure: fields.max_total_exposure, max_daily_loss: fields.max_daily_loss, max_weekly_loss: fields.max_weekly_loss,
          max_drawdown: fields.max_drawdown, cost_allowance: fields.cost_allowance, max_leverage: fields.max_leverage,
          max_open_positions: Number(fields.max_open_positions), max_trades_per_day: Number(fields.max_trades_per_day), max_trades_total: Number(fields.max_trades_total),
        },
      },
    };
    if (!experimentCreateRequest(body) || !validBounds(body.configuration.risk_limits, body.configuration.sample_target)) { setNotice("Correct the draft bounds before submitting."); return; }
    try { sessionStorage.setItem(scope, JSON.stringify(body)); } catch { setNotice("Draft recovery storage unavailable. Try again when browser storage is available."); return; }
    setPending(body); setBusy(true); setNotice(null);
    const abort = new AbortController(); controller.current = abort;
    try {
      const result = await experimentCreate(body, { signal: abort.signal });
      if (abort.signal.aborted) return;
      sessionStorage.removeItem(scope); setPending(null); setCreated(result.experiment_id);
    } catch (cause) {
      if (abort.signal.aborted) return;
      // A retry refusal cannot prove whether the earlier ambiguous attempt committed.
      if (!original && rejectedBeforeCommit(cause, "draft")) {
        sessionStorage.removeItem(scope); setPending(null); setNotice("Draft rejected. Correct its bounds or permissions before submitting again.");
      } else setNotice("Draft could not be confirmed. Recover its original request before creating another.");
    }
    finally { if (!abort.signal.aborted) { controller.current = null; setBusy(false); } }
  }
  return <section className="space-y-2 rounded-card border border-border-subtle p-4" data-testid="experiment-draft">
    <h2 className="font-semibold">Bounded experiment</h2>
    <p className="text-sm text-text-muted">Use this saved strategy for research. Internal simulation remains separate from BloFin; creating or starting a draft does not activate trading.</p>
    {created ? <Link className="inline-flex min-h-11 items-center text-accent" href={`/strategies?experiment=${encodeURIComponent(created)}`}>Open experiment</Link>
      : pending ? <div role="status"><p className="text-sm">A draft is awaiting confirmation. Its original configuration and identity are retained.</p><Button size="sm" disabled={busy} onClick={() => void create(pending)}>Recover draft</Button></div>
      : loading ? <p role="status">Loading authored versions…</p> : error ? <p role="status">Experiment prerequisites unavailable.</p>
      : !account?.enabled ? <p role="status">An existing enabled paper account is required. No account or settings have been changed.</p>
      : !versions.length ? <p role="status">A complete supported authored spec is required. <Link href={`/agent?strategy_id=${strategyId}`} className="text-accent">Continue with Agent</Link>.</p>
      : <details><summary className="min-h-11 cursor-pointer">Prepare research draft</summary>
        <form className="space-y-3 pt-2" onSubmit={event => { event.preventDefault(); void create(); }}>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="text-sm">Name<input aria-label="Experiment name" required maxLength={120} value={title} onChange={event => setTitle(event.target.value)} className="block min-h-11 w-full rounded-control bg-surface-0 p-2" /></label>
            <label className="text-sm">Mode<select aria-label="Experiment mode" value={mode} onChange={event => setMode(event.target.value as Configuration["mode"])} className="block min-h-11 w-full bg-surface-0"><option value="exploration">Exploration</option><option value="validation">Validation</option></select></label>
            <label className="text-sm">Authored version<select aria-label="Authored version" value={versionId} onChange={event => setVersionId(event.target.value)} className="block min-h-11 w-full bg-surface-0">{versions.map(item => <option key={item.id} value={item.id}>Version {item.version}</option>)}</select></label>
          </div>
          <p className="text-xs text-text-muted">One immutable baseline variant. Minimum and maximum are setup observations. Monetary bounds are USDT, not account performance.</p>
          <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {field("minimum", "Minimum observations", "number")}{field("maximum", "Maximum observations", "number")}
            {moneyFields.map(([key, label]) => field(key, `${label} (USDT)`))}
            {field("max_leverage", "Maximum leverage")}{field("max_open_positions", "Maximum open positions", "number")}
            {field("max_trades_per_day", "Maximum trades per day", "number")}{field("max_trades_total", "Maximum trades total", "number")}
          </div>
          <p className="text-xs text-text-muted">Models disabled. Exact bounds require separate Owner approval before lifecycle start; research has no dispatch authority.</p>
          <Button type="submit" size="sm" disabled={busy}>Create experiment draft</Button>
        </form>
      </details>}
    {notice ? <p role="status" className="text-sm">{notice}</p> : null}
  </section>;
}
