"use client";

import Link from "next/link";
import { ManualDemoActivity } from "@/components/settings/ManualDemoActivity";
import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { ApiError } from "@/lib/api/client";
import { manualDemo, type ManualDemoInstrument, type ManualDemoPreview, type ManualDemoStatus } from "@/lib/api/manual-demo";
import { validateManualDemoInput } from "@/lib/manual-demo-validation";

type DemoFailure = {
  message: string;
  code?: string;
  stage?: string;
  reason?: string;
  rewardRisk?: number;
  category?: string;
};

function object(value: unknown): Record<string, unknown> | undefined {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : undefined;
}

function diagnosticCode(value: unknown): string | undefined {
  return typeof value === "string" && /^[a-z][a-z0-9_]{0,79}$/.test(value)
    ? value
    : undefined;
}

function demoFailure(failure: unknown): DemoFailure {
  if (!(failure instanceof ApiError)) {
    return {
      message:
        "Demo request failed. Check connectivity before retrying preview. Do not repeat an uncertain confirmation; recover the same plan instead.",
    };
  }
  const envelope = object(object(failure.body)?.error);
  const details = object(envelope?.details);
  const preflight = object(details?.preflight);
  const rawRatio = details?.gross_reward_risk;
  const ratio =
    typeof rawRatio === "string" && /^\d+(?:\.\d+)?$/.test(rawRatio)
      ? Number(rawRatio)
      : typeof rawRatio === "number"
        ? rawRatio
        : undefined;
  return {
    message: failure.message,
    code: diagnosticCode(envelope?.code),
    category: diagnosticCode(details?.category),
    stage: diagnosticCode(preflight?.stage),
    reason:
      diagnosticCode(preflight?.reason_code) ?? diagnosticCode(details?.reason),
    rewardRisk:
      ratio !== undefined && Number.isFinite(ratio) && ratio >= 0
        ? ratio
        : undefined,
  };
}

export function ManualDemoTest() {
  const [open, setOpen] = useState(false);
  const [instrument, setInstrument] = useState<ManualDemoInstrument | null>(null);
  const [side, setSide] = useState<"BUY" | "SELL">("BUY");
  const [quantity, setQuantity] = useState("");
  const [stop, setStop] = useState("");
  const [target, setTarget] = useState("");
  const [preview, setPreview] = useState<ManualDemoPreview | null>(null);
  const [result, setResult] = useState<ManualDemoStatus | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<DemoFailure | null>(null);
  const pending = useRef(false);
  const [sent, setSent] = useState(false);

  const input = { symbol: "BTCUSDT" as const, side, order_type: "MARKET" as const, quantity, stop, target };
  const inputError = instrument && quantity && stop && target ? validateManualDemoInput(input, instrument) : null;

  async function loadInstrument() {
    setInstrument(null);
    setPreview(null);
    setConfirmed(false);
    setInstrument(await manualDemo.instrument());
  }

  async function submitConfirmed(plan: ManualDemoPreview) {
    setSent(true);
    try {
      setResult(await manualDemo.confirm(plan));
    } catch (failure) {
      // Only explicit server proof of pre-claim refusal permits a fresh plan.
      // Network loss and post-submit errors retain the exact recovery identity.
      if (failure instanceof ApiError && object(object(object(failure.body)?.error)?.details)?.submission === "not_started") {
        setSent(false);
        setPreview(null);
        setConfirmed(false);
      }
      throw failure;
    }
  }

  async function act(operation: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    try { await operation(); }
    catch (failure) { setError(demoFailure(failure)); }
    finally { pending.current = false; setBusy(false); }
  }
  function changed(update: () => void) {
    update();
    setPreview(null);
    setConfirmed(false);
  }

  return <Card>
    <CardHeader><p className="text-sm">Supervised manual BloFin demo test</p></CardHeader>
    <CardContent className="space-y-3">
      <p className="text-sm text-text-muted">Owner only · BTC market entry · demo funds. Preview and exact confirmation are required. This does not approve a strategy. The server capability must be separately armed after review.</p>
      <p className="text-sm text-text-muted">Manual connectivity exception: minimum 1R does not apply; excluded from strategy performance. Strategy plans still require minimum 1R.</p>
      <p className="text-sm text-text-muted">Manual demo limits: fresh demo funds only · maximum test notional 5% and planned loss 1% of available demo equity · 1× leverage · flat demo account with no pending orders. Strategy daily PnL, trade count and green day rules do not apply.</p>
      {!open ? <Button onClick={() => { setOpen(true); void act(loadInstrument); }}>Prepare manual demo test</Button> : <>
        {!sent && <div className="space-y-2">
          {instrument ? <div aria-label="Exchange contract limits">
            <p>Exchange minimum: {instrument.minimum_quantity} contracts · lot increment: {instrument.lot_increment} contracts · price increment: {instrument.tick_size} USDT</p>
            <p>1 contract = {instrument.contract_multiplier} BTC. Contracts are exchange units; quantity is not BTC.</p>
            <p>BTC equivalent: {Number.isFinite(Number(quantity)) ? Number(quantity) * Number(instrument.contract_multiplier) : "—"} BTC · approximate notional: {Number.isFinite(Number(quantity)) ? (Number(quantity) * Number(instrument.contract_multiplier) * Number(instrument.reference_price)).toFixed(2) : "—"} USDT</p>
            <p>Indicative entry: {instrument.reference_price} USDT · minimum test notional {instrument.minimum_notional} USDT. Preview rechecks fresh executable depth and account capacity.</p>
          </div> : <p>Exchange instrument limits must be loaded before preview.</p>}
          <Button disabled={busy} onClick={() => void act(loadInstrument)}>Refresh instrument limits</Button>
          <label className="block">Side <select aria-label="Demo side" disabled={busy} value={side} onChange={(e) => changed(() => setSide(e.target.value as "BUY" | "SELL"))}><option value="BUY">Long</option><option value="SELL">Short</option></select></label>
          <label className="block">Quantity in contracts <input aria-label="Quantity in contracts" inputMode="decimal" disabled={busy} value={quantity} onChange={(e) => changed(() => setQuantity(e.target.value))} /></label>
          <label className="block">Stop (USDT) <input aria-label="Stop (USDT)" inputMode="decimal" disabled={busy} value={stop} onChange={(e) => changed(() => setStop(e.target.value))} /></label>
          <label className="block">Target (USDT) <input aria-label="Target (USDT)" inputMode="decimal" disabled={busy} value={target} onChange={(e) => changed(() => setTarget(e.target.value))} /></label>
          {inputError && <p role="alert">{inputError}</p>}
          <Button disabled={busy || !instrument || !quantity || !stop || !target || Boolean(inputError)} onClick={() => void act(async () => { setPreview(null); setConfirmed(false); setPreview(await manualDemo.preview(input)); })}>Preview demo entry</Button>
        </div>}
        {preview && <div className="space-y-2" aria-label="Manual demo entry preview">
          <p>{preview.instrument} · {preview.side === "BUY" ? "Long" : "Short"} · Market</p>
          <p>{preview.quantity} contracts ({preview.base_quantity} BTC) · reference {preview.reference_price} USDT</p>
          <p>Permitted entry range: {preview.entry_lower}–{preview.entry_upper} USDT</p>
          <p>Stop {preview.stop} · target {preview.target} USDT</p>
          <p>Maximum planned loss: {preview.maximum_planned_loss} USDT · gross reward/risk: {Number(preview.gross_reward_risk).toFixed(2)}R</p>
          <p>Preview expires: {new Date(preview.valid_until).toLocaleTimeString()}</p>
          {preview.warnings.map((warning) => <p className="text-sm text-text-muted" key={warning}>{warning}</p>)}
          {!sent && <>
            <label className="block"><input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} /> I confirm this exact manual demo test plan.</label>
            <Button disabled={busy || !confirmed} onClick={() => void act(() => submitConfirmed(preview))}>Confirm and submit demo market order</Button>
          </>}
          {sent && !result && <Button disabled={busy} onClick={() => void act(() => submitConfirmed(preview))}>Recover this exact confirmation (no resend)</Button>}
        </div>}
        {result && <div role="status" className="space-y-2">
          <p>Manual demo test: {result.status.replaceAll("_", " ")}</p>
          <Link className="underline" href={`/execution/manual-demo/${encodeURIComponent(result.command_id)}`}>Open persistent attempt detail</Link>
          <p>Actual filled contracts: {result.filled_quantity} · remaining: {result.remaining_quantity}</p>
          <p>Actual fill price: {result.average_fill_price ?? "Not verified"} · fees: {result.fees ?? "Not verified"} USDT</p>
          <p>Stop/target protection: {result.protection}</p>
          {result.reconciliation_diagnostics?.map((diagnostic, index) => (
            <p role="alert" key={`${diagnostic.stage}:${diagnostic.reason_code}:${index}`}>
              Reconciliation: {diagnostic.stage} — {diagnostic.reason_code}. {diagnostic.endpoint_name}
              {diagnostic.field_name ? `; field ${diagnostic.field_name}` : ""}
              {diagnostic.http_status ? `; HTTP ${diagnostic.http_status}` : ""}
              {diagnostic.venue_error_code ? `; venue code ${diagnostic.venue_error_code}` : ""}.
              Refresh this same command after checking its venue evidence. Do not resubmit.
            </p>
          ))}
          {result.missing_evidence.map((warning) => <p key={warning}>{warning}</p>)}
          <Button disabled={busy} onClick={() => void act(async () => { setResult(await manualDemo.reconcile(result.command_id)); })}>Refresh venue evidence</Button>
          {result.can_cancel && <Button disabled={busy} onClick={() => void act(async () => { setResult(await manualDemo.cancel(result.command_id)); })}>Cancel unfilled entry remainder</Button>}
          <details><summary>Stored evidence</summary><p>Venue order: {result.venue_order_id ? "Recorded on venue" : "Not verified"}</p><p>Protection orders: {result.protection_order_ids?.length ? "Recorded on venue" : "Not verified"}</p><p>Journal: {result.journal_trade_id ? "Fill projection recorded" : "No actual fill recorded"}</p></details>
        </div>}
      </>}
      <ManualDemoActivity refreshKey={result?.command_id} />
      {error && <div role="alert" className="space-y-1">
        <p>{error.message}</p>
        {error.category && <p>Policy group: {error.category.replaceAll("_", " ")}</p>}
        {error.code && <p>Error code: {error.code}</p>}
        {error.stage && <p>Blocked stage: {error.stage}</p>}
        {error.reason && <p>Blocking reason: {error.reason}</p>}
        {error.rewardRisk !== undefined && <p>Calculated gross reward/risk: {error.rewardRisk.toFixed(2)}R</p>}
      </div>}
    </CardContent>
  </Card>;
}
