"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { StatusBadge } from "@/components/StatusBadge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Row, rowBadgeClass } from "@/components/ui/row";
import { api } from "@/lib/api";
import type { WatcherSymbolRuntimeStatus, WatcherWatchlistSlot } from "@/lib/api/types";

export const DEFAULT_WATCHLIST_SLOTS: WatcherWatchlistSlot[] = [
  { position: 1, symbol: "BTCUSDT", enabled: true },
  { position: 2, symbol: "ZECUSDT", enabled: true },
  { position: 3, symbol: "ETHUSDT", enabled: true },
  { position: 4, symbol: "TAOUSDT", enabled: true },
  { position: 5, symbol: "HYPEUSDT", enabled: true },
];

function statusLabel(errorState: string | null, marketSource: string): string {
  if (errorState === "unsupported_contract") return "Unsupported";
  if (errorState === "provider_unreachable") return "Provider unreachable";
  if (errorState === "awaiting_contract_check") return "Awaiting contract check";
  if (errorState) return "Unavailable";
  return marketSource;
}

function moveSlot(slots: WatcherWatchlistSlot[], index: number, direction: -1 | 1) {
  const next = index + direction;
  if (next < 0 || next >= slots.length) return slots;
  const copy = slots.slice();
  const current = copy[index];
  const neighbor = copy[next];
  if (!current || !neighbor) return slots;
  copy[index] = { ...neighbor, position: current.position };
  copy[next] = { ...current, position: neighbor.position };
  return copy.map((slot, slotIndex) => ({ ...slot, position: slotIndex + 1 }));
}

export function WatcherWatchlistEditor({
  slots,
  statuses,
  saving = false,
  configurationRevision = 0,
  staleAfterSeconds = 90,
  message = null,
  onChange,
  onSave,
}: {
  slots: WatcherWatchlistSlot[];
  statuses: WatcherSymbolRuntimeStatus[];
  saving?: boolean;
  configurationRevision?: number;
  staleAfterSeconds?: number;
  message?: string | null;
  onChange: (slots: WatcherWatchlistSlot[]) => void;
  onSave: () => void;
}) {
  const [, setExpiryTick] = useState(0);
  const statusBySymbol = new Map(statuses.filter((row) => {
    const age = row.observed_at ? Date.now() - Date.parse(row.observed_at) : Number.NaN;
    return row.configuration_revision === configurationRevision && age >= 0 && age < staleAfterSeconds * 1000;
  }).map((row) => [row.symbol.trim().toUpperCase(), row]));

  // One local timer expires displayed rows even when the network poll is stalled.
  // Replace it on every render so refreshed observations and edited slots own it.
  useEffect(() => {
    const deadlines = slots.flatMap((slot) => {
      const row = statusBySymbol.get(slot.symbol.trim().toUpperCase());
      return row?.enabled === slot.enabled && row.observed_at
        ? [Date.parse(row.observed_at) + staleAfterSeconds * 1000]
        : [];
    });
    const nextExpiry = Math.min(...deadlines);
    if (!Number.isFinite(nextExpiry)) return;
    const timer = setTimeout(
      () => setExpiryTick((tick) => tick + 1),
      Math.min(2147483647, Math.max(0, nextExpiry - Date.now())),
    );
    return () => clearTimeout(timer);
  });

  return (
    <Card data-testid="watcher-watchlist">
      <CardHeader>
        <CardTitle className="text-base">Watcher markets</CardTitle>
        <p className="text-xs text-text-muted">
          Five paper markets, scanned in order. Unsupported markets stay unavailable.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {slots.map((slot, index) => {
          const found = statusBySymbol.get(slot.symbol.trim().toUpperCase());
          const status = found?.enabled === slot.enabled ? found : undefined;
          return (
            <Row
              key={slot.position}
              className="border-b border-border pb-3 sm:grid-cols-[3.5rem_auto_minmax(0,1fr)_auto]"
              data-testid={`watchlist-slot-${slot.position}`}
              label={<span className="text-xs">Slot {slot.position}</span>}
              value={
                <div className="flex flex-wrap items-baseline gap-2">
                  <Input
                    aria-label={`Symbol for slot ${slot.position}`}
                    value={slot.symbol}
                    disabled={saving}
                    className="w-36"
                    onChange={(event) => {
                      const next = slots.slice();
                      const current = next[index];
                      if (!current) return;
                      next[index] = { ...current, symbol: event.target.value.toUpperCase() };
                      onChange(next);
                    }}
                  />
                  <label className="flex items-baseline gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={slot.enabled}
                      disabled={saving}
                      aria-label={`Enable slot ${slot.position}`}
                      onChange={(event) => {
                        const next = slots.slice();
                        const current = next[index];
                        if (!current) return;
                        next[index] = { ...current, enabled: event.target.checked };
                        onChange(next);
                      }}
                    />
                    {slot.enabled ? "Enabled" : "Disabled"}
                  </label>
                </div>
              }
              meta={
                <span
                  className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-zinc-300"
                  data-testid={`watchlist-status-${slot.position}`}
                >
                  {status ? (
                    <>
                      <StatusBadge
                        label={statusLabel(status.error_state, status.market_source)}
                        tone={status.error_state ? "warn" : "healthy"}
                        className={rowBadgeClass}
                      />
                      <span>
                        {status.freshness.replace(/_/g, " ")} · {status.setup_state.replace(/_/g, " ")}
                        {status.alert_state !== "none" ? ` · ${status.alert_state.replace(/_/g, " ")}` : ""}
                        {status.error_state ? ` · ${status.error_state.replace(/_/g, " ")}` : ""}
                      </span>
                    </>
                  ) : (
                    "Pending / unscanned"
                  )}
                </span>
              }
              action={
                <>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    aria-label={`Move slot ${slot.position} up`}
                    disabled={saving || index === 0}
                    onClick={() => onChange(moveSlot(slots, index, -1))}
                  >
                    Up
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    aria-label={`Move slot ${slot.position} down`}
                    disabled={saving || index === slots.length - 1}
                    onClick={() => onChange(moveSlot(slots, index, 1))}
                  >
                    Down
                  </Button>
                </>
              }
            />
          );
        })}
        <div className="flex items-center gap-3">
          <Button type="button" onClick={onSave} disabled={saving} data-testid="watchlist-save">
            {saving ? "Saving" : "Save watchlist"}
          </Button>
          {message ? <p className="text-xs text-text-muted">{message}</p> : null}
        </div>
      </CardContent>
    </Card>
  );
}

export function WatcherWatchlistSection() {
  const [slots, setSlots] = useState<WatcherWatchlistSlot[]>([]);
  const [revision, setRevision] = useState<number | null>(null);
  const [statuses, setStatuses] = useState<WatcherSymbolRuntimeStatus[]>([]);
  const [staleAfter, setStaleAfter] = useState(90);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const generation = useRef(0);
  const refreshRequest = useRef<number | null>(null);
  const pollRequest = useRef<number | null>(null);
  const mounted = useRef(false);
  const savingRef = useRef(false);

  const refresh = useCallback(async () => {
    const request = ++generation.current;
    refreshRequest.current = request;
    pollRequest.current = null;
    try {
      const [config, status] = await Promise.all([
        api.watcherWatchlist.configuration(), api.watcherWatchlist.status().catch(() => null),
      ]);
      if (!mounted.current || request !== generation.current) return;
      setSlots(config.slots);
      setRevision(config.revision);
      setStatuses(status?.configuration_revision === config.revision ? status.symbols : []);
      if (status) setStaleAfter(status.stale_after_seconds);
      setMessage(status ? null : "Market availability unavailable. Your saved watchlist is shown.");
      return status !== null;
    } finally {
      if (refreshRequest.current === request) refreshRequest.current = null;
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh().catch(() => {
      if (mounted.current) setMessage("Watchlist configuration unavailable. Reload to try again.");
    });
    // Leave capacity in the shared 120/hour read quota for configuration and saves.
    const timer = setInterval(() => {
      if (savingRef.current || refreshRequest.current !== null || pollRequest.current !== null) return;
      const request = ++generation.current;
      pollRequest.current = request;
      void api.watcherWatchlist.status().then((status) => {
        if (mounted.current && request === generation.current) {
          setStatuses(status.symbols);
          setStaleAfter(status.stale_after_seconds);
        }
      }).catch(() => {
        if (mounted.current && request === generation.current) setStatuses([]);
      }).finally(() => {
        if (pollRequest.current === request) pollRequest.current = null;
      });
    }, 60000);
    return () => {
      mounted.current = false;
      clearInterval(timer);
    };
  }, [refresh]);

  return (
    <WatcherWatchlistEditor
      slots={slots}
      statuses={statuses}
      configurationRevision={revision ?? -1}
      staleAfterSeconds={staleAfter}
      saving={saving || revision === null}
      message={message}
      onChange={setSlots}
      onSave={() => {
        if (revision === null || savingRef.current) return;
        savingRef.current = true;
        setSaving(true);
        ++generation.current;
        pollRequest.current = null;
        setStatuses([]);
        setMessage(null);
        void api.watcherWatchlist
          .replace(slots.map((slot) => ({ symbol: slot.symbol, enabled: slot.enabled })), revision)
          .then(async () => {
            const availabilityLoaded = await refresh();
            if (mounted.current) setMessage(availabilityLoaded
              ? "Watchlist saved. Paper only."
              : "Watchlist saved. Market availability unavailable. Paper only.");
          })
          .catch(() => {
            if (mounted.current) setMessage("Save or refresh failed. Reload before trying again.");
          })
          .finally(() => {
            savingRef.current = false;
            if (mounted.current) setSaving(false);
          });
      }}
    />
  );
}
