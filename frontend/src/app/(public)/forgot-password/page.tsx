"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { ErrorState, SuccessState } from "@/components/states";
import { api, ApiError } from "@/lib/api";

const ERROR_ID = "forgot-password-error";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const response = await api.auth.requestPasswordReset(email);
      setMessage(response.message);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-start gap-3 px-4 py-4 [@media(min-height:600px)]:justify-center [@media(min-height:600px)]:gap-6 [@media(min-height:600px)]:py-10">
      <h1 className="text-2xl font-semibold text-zinc-50">Forgot password</h1>
      <form onSubmit={onSubmit} className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/70 p-5">
        <div className="space-y-2">
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? ERROR_ID : undefined}
            required
          />
        </div>
        {error ? <ErrorState id={ERROR_ID} message={error} /> : null}
        {message ? <SuccessState message={message} /> : null}
        <Button type="submit" disabled={busy} className="w-full">
          Send reset link
        </Button>
      </form>
      <p className="text-sm text-zinc-400">
        <Link href="/login" className="text-emerald-400 hover:underline">
          Back to sign in
        </Link>
      </p>
    </div>
  );
}
