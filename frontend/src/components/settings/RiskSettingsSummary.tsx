"use client";

import { useCallback } from "react";

import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import { formatAmount, formatCount, formatPercent, parseDecimal } from "@/lib/format";
import { SettingsReadout, SettingsUnavailable } from "./SettingsReadout";

const ACCOUNT_UNIT = "USDT";

export function RiskSettingsSummary() {
  const loader = useCallback(() => api.risk.settings(), []);
  const { data, loading, error, reload } = useAsyncData(loader, []);
  const enabled = (value: boolean) => (value ? "On" : "Off");
  // Risk settings carry no currency; paper and BloFin demo accounts settle in USDT.
  const amount = (value: string | null) =>
    value === null ? "Not set" : formatAmount(value, ACCOUNT_UNIT);

  return (
    <Card data-testid="settings-risk-summary">
      <CardHeader>
        <p className="text-sm text-text-muted">
          Your saved paper risk parameters. Display only; the existing risk
          engine remains authoritative.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {loading || error || !data ? (
          <SettingsUnavailable
            label="Risk settings"
            loading={loading}
            onRetry={() => void reload()}
          />
        ) : (
          <>
            <SettingsReadout
              rows={[
                ["Daily loss limit", amount(data.daily_loss_limit)],
                ["Daily profit target", amount(data.daily_target)],
                ["Maximum trades per day", formatCount(data.max_trades_per_day)],
                [
                  "Maximum risk per trade",
                  formatPercent(parseDecimal(data.max_risk_per_trade_percent), 2, {
                    alreadyPercent: true,
                    trimZeros: true,
                  }),
                ],
                ["Default account balance", amount(data.default_account_balance)],
                ["Risk day timezone", data.timezone],
                [
                  "Green day protection",
                  enabled(data.green_day_protection_enabled),
                ],
                ["Stop after one loss", enabled(data.one_loss_stop_enabled)],
                ["Overtrading guard", enabled(data.overtrading_guard_enabled)],
              ]}
            />
            {data.using_defaults ? (
              <p className="text-xs text-text-muted">Using system defaults.</p>
            ) : null}
            {data.timezone_fallback ? (
              <p className="text-xs text-amber-300">
                Timezone fallback is in use.
              </p>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
