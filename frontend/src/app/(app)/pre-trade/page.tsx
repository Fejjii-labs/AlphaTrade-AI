"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LossAcceptancePanel } from "@/components/strategy/LossAcceptancePanel";
import { ErrorState } from "@/components/states";
import { api } from "@/lib/api";
import type { PreTradeAnalyzeResponse, PositionSizingResult } from "@/lib/api/types";
import { formatDecimal } from "@/lib/utils";

export default function PreTradePage() {
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [accountSize, setAccountSize] = useState("10000");
  const [analysis, setAnalysis] = useState<PreTradeAnalyzeResponse | null>(null);
  const [sizing, setSizing] = useState<PositionSizingResult | null>(null);
  const [lossAccepted, setLossAccepted] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function runAnalysis() {
    setBusy(true);
    setError(null);
    try {
      const result = await api.pretrade.analyze({
        symbol,
        exchange: "mock",
        direction: "long",
        account_size: accountSize,
        max_risk_per_trade: "1",
      });
      setAnalysis(result);
      setSizing(result.position_size ?? null);
      setLossAccepted(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Analysis failed");
    } finally {
      setBusy(false);
    }
  }

  async function confirmLoss(accepted: boolean) {
    setLossAccepted(accepted);
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold">Pre-Trade Analysis</h1>
        <p className="text-sm text-zinc-400">
          Deterministic analysis, position sizing, and loss acceptance (paper only — no execution).
        </p>
      </div>

      <Card>
        <CardContent className="grid gap-4 pt-6 sm:grid-cols-3">
          <div className="space-y-2">
            <Label htmlFor="symbol">Symbol</Label>
            <Input id="symbol" value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="account">Account size</Label>
            <Input id="account" value={accountSize} onChange={(e) => setAccountSize(e.target.value)} />
          </div>
          <div className="flex items-end">
            <Button onClick={() => void runAnalysis()} disabled={busy}>
              {busy ? "Analyzing…" : "Analyze setup"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {error ? <ErrorState message={error} /> : null}

      {analysis ? (
        <section className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Analysis</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm text-zinc-300">
              <p>Regime: {analysis.market_regime}</p>
              <p>Confidence: {analysis.setup_confidence_score.toFixed(0)}%</p>
              <p>Recommendation: {analysis.final_recommendation.replace("_", " ")}</p>
              {analysis.suggested_stop_loss ? (
                <p>Stop: {formatDecimal(analysis.suggested_stop_loss)}</p>
              ) : null}
            </CardContent>
          </Card>

          {sizing ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Position sizing</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm text-zinc-300">
                <p>Planned loss: {formatDecimal(sizing.planned_loss_amount)}</p>
                <p>Size: {formatDecimal(sizing.confidence_adjusted_size)}</p>
                <p>{sizing.worst_case_scenario}</p>
              </CardContent>
            </Card>
          ) : null}
        </section>
      ) : null}

      {sizing ? (
        <LossAcceptancePanel sizing={sizing} onAccepted={confirmLoss} />
      ) : null}

      {lossAccepted === false ? (
        <p className="text-sm text-amber-400">Paper execution would remain blocked until size adjusted.</p>
      ) : null}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Human vs system</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-text-secondary">
          After a trade is journaled, compare your plan adherence with the system&apos;s on{" "}
          <Link href="/journal/comparison" className="text-accent underline">
            Human vs system
          </Link>
          . Read-only; nothing is executed from there.
        </CardContent>
      </Card>
    </div>
  );
}
