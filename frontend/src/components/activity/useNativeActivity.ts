"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "@/contexts/AuthContext";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";
import { ApiError } from "@/lib/api/client";
import { readNativeActivity, type NativeActivityKind, type NativeActivityPage } from "@/lib/api/blofin-activity";
import { mergeNativePages, nativeAccount } from "@/lib/native-activity";

type Snapshot = { scope: string; kind: NativeActivityKind; page: NativeActivityPage | null };

export function useNativeActivity(kind: NativeActivityKind, refreshKey: number) {
  const { user, organization } = useAuth();
  const [generation, setGeneration] = useState(sessionGeneration);
  const context = JSON.stringify([organization?.id, user?.id, generation]);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const request = useRef<AbortController | null>(null);
  const current = useRef(context);
  current.current = context;
  const page = snapshot?.scope === context && snapshot.kind === kind ? snapshot.page : null;

  useEffect(() => onSessionCleared(() => {
    request.current?.abort();
    setSnapshot(null);
    setError(null);
    setBusy(false);
    setGeneration(sessionGeneration());
  }), []);

  const load = useCallback(async (cursor?: string, expectedAccount?: string) => {
    request.current?.abort();
    if (!user?.id || !organization?.id) { setSnapshot(null); setBusy(false); setError(null); return; }
    const controller = new AbortController();
    request.current = controller;
    const requestGeneration = sessionGeneration();
    const active = () => !controller.signal.aborted && current.current === context &&
      requestGeneration === sessionGeneration();
    setBusy(true);
    setError(null);
    // A fresh binding must be verified before showing a previous account's cached rows.
    if (!cursor) setSnapshot(null);
    try {
      const result = await readNativeActivity({ kind, limit: 20, cursor }, { signal: controller.signal });
      if (!active()) return;
      if (result.organization_id !== organization.id) {
        setSnapshot(null);
        setError("Activity account changed. Refresh to verify the current connection.");
        return;
      }
      if (cursor && (result.identity_status !== "verified" || nativeAccount(result) !== expectedAccount)) {
        setSnapshot(null);
        setError("Activity account changed. Refresh to verify the current connection.");
        return;
      }
      setSnapshot(previous => ({ scope: context, kind,
        page: mergeNativePages(cursor && previous?.scope === context && previous.kind === kind ? previous.page : null, result) }));
    } catch (failure) {
      if (!active()) return;
      // Foreign/stale continuations can indicate a changed configured native UID.
      if (failure instanceof ApiError && [401, 403, 404, 422].includes(failure.status)) setSnapshot(null);
      setError("Native activity unavailable. Refresh to retry the stored history.");
    } finally {
      if (active()) setBusy(false);
    }
  }, [context, kind, organization?.id, user?.id]);

  useEffect(() => {
    setSnapshot(null);
    setError(null);
    void load();
    return () => { request.current?.abort(); };
  }, [load, refreshKey]);

  return { page, busy, error, refresh: () => void load(),
    more: () => page?.next_cursor && void load(page.next_cursor, nativeAccount(page)) };
}
