"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ErrorState, SuccessState } from "@/components/states";
import { useAuth } from "@/contexts/AuthContext";
import { api, ApiError } from "@/lib/api";

export function EmailVerificationNotice() {
  const { user, refreshProfile } = useAuth();
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!user || user.email_verified) {
    return null;
  }

  async function resend() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await api.auth.requestVerifyEmail();
      setMessage(response.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not send verification email.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-xl border border-amber-500/40 bg-amber-500/10 p-4 text-sm text-amber-100">
      <p className="font-medium">Verify your email</p>
      <p className="mt-1 text-amber-200/90">
        We sent a confirmation link to {user.email}. Check your inbox.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        <Button type="button" variant="secondary" size="sm" disabled={busy} onClick={() => void resend()}>
          Resend verification email
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={() => void refreshProfile()}>
          Refresh status
        </Button>
      </div>
      {message ? <SuccessState className="mt-3" message={message} /> : null}
      {error ? <ErrorState className="mt-3" message={error} /> : null}
    </div>
  );
}
