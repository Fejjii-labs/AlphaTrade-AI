"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { PaperModeBanner } from "@/components/PaperModeBanner";
import { ErrorState } from "@/components/states";
import { getAuthErrorMessage, useAuth } from "@/contexts/AuthContext";

const ERROR_ID = "login-error";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      // Read at submit time (not render) so no Suspense boundary is needed.
      const next = new URLSearchParams(window.location.search).get("next");
      await login(email, password, next ?? undefined);
    } catch (err) {
      setError(getAuthErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-start gap-3 px-4 py-4 [@media(min-height:600px)]:justify-center [@media(min-height:600px)]:gap-6 [@media(min-height:600px)]:py-10">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-50">Sign in</h1>
      </div>
      <PaperModeBanner compact />
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
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <Label htmlFor="password">Password</Label>
            <Link href="/forgot-password" className="text-xs text-emerald-400 hover:underline">
              Forgot password?
            </Link>
          </div>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? ERROR_ID : undefined}
            required
          />
        </div>
        {error ? <ErrorState id={ERROR_ID} message={error} /> : null}
        <Button type="submit" disabled={busy} className="w-full">
          Sign in
        </Button>
      </form>
      <p className="text-sm text-zinc-400">
        Need an account?{" "}
        <Link href="/register" className="text-emerald-400 hover:underline">
          Register
        </Link>
      </p>
    </div>
  );
}
