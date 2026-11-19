"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button } from "@/components/ui/button";
import { FieldHint, Input, Label } from "@/components/ui/input";
import { ErrorState, SuccessState } from "@/components/states";
import { api } from "@/lib/api";
import { friendlyError } from "@/lib/api/client";

const ERROR_ID = "reset-password-error";
const PASSWORD_HINT_ID = "reset-password-hint";

function ResetPasswordForm() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!token) {
      setError("Missing reset token. Open the link from your email.");
      return;
    }
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await api.auth.confirmPasswordReset(token, password);
      setMessage(response.message);
    } catch (err) {
      setError(friendlyError(err, "Reset failed."));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-start gap-3 px-4 py-4 [@media(min-height:600px)]:justify-center [@media(min-height:600px)]:gap-6 [@media(min-height:600px)]:py-10">
      <h1 className="text-2xl font-semibold text-zinc-50">Reset password</h1>
      <form onSubmit={onSubmit} className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/70 p-5">
        <div className="space-y-2">
          <Label htmlFor="password">New password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            minLength={12}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? `${PASSWORD_HINT_ID} ${ERROR_ID}` : PASSWORD_HINT_ID}
            required
          />
          <FieldHint id={PASSWORD_HINT_ID}>Use at least 12 characters.</FieldHint>
        </div>
        {error ? <ErrorState id={ERROR_ID} message={error} /> : null}
        {message ? <SuccessState message={message} /> : null}
        <Button type="submit" disabled={busy || !token} className="w-full">
          Update password
        </Button>
      </form>
      <p className="text-sm text-zinc-400">
        <Link href="/login" className="text-emerald-400 hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<p className="p-10 text-sm text-zinc-400">Loading…</p>}>
      <ResetPasswordForm />
    </Suspense>
  );
}
