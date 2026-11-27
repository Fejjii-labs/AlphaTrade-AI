"use client";

import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { ErrorState } from "@/components/states";
import { getAuthErrorMessage, useAuth } from "@/contexts/AuthContext";
import type { TwoFactorChallengeResponse } from "@/lib/api/types";

const ERROR_ID = "login-error";
const CODE_ERROR_ID = "login-code-error";

function nextFromLocation(): string | undefined {
  // Read at submit time (not render) so no Suspense boundary is needed.
  return new URLSearchParams(window.location.search).get("next") ?? undefined;
}

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState<TwoFactorChallengeResponse | null>(null);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const outcome = await login(email, password, nextFromLocation());
      if (outcome.status === "two_factor_required") {
        setPassword("");
        setChallenge(outcome.challenge);
      }
    } catch (err) {
      setError(getAuthErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  if (challenge) {
    return (
      <SecondFactorStep
        email={email}
        challenge={challenge}
        onBack={() => {
          setChallenge(null);
          setError(null);
        }}
      />
    );
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-start gap-3 px-4 py-4 [@media(min-height:600px)]:justify-center [@media(min-height:600px)]:gap-6 [@media(min-height:600px)]:py-10">
      <h1 className="text-2xl font-semibold text-zinc-50">Sign in</h1>
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

function SecondFactorStep({
  email,
  challenge,
  onBack,
}: {
  email: string;
  challenge: TwoFactorChallengeResponse;
  onBack: () => void;
}) {
  const { completeTwoFactorLogin } = useAuth();
  const [useRecovery, setUseRecovery] = useState(false);
  const [value, setValue] = useState("");
  const [remember, setRemember] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const factor = useRecovery ? { recoveryCode: value.trim() } : { code: value.replace(/\s/g, "") };
      await completeTwoFactorLogin(email, challenge.pending_token, factor, remember, nextFromLocation());
    } catch (err) {
      setError(getAuthErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const inputId = useRecovery ? "recovery-code" : "totp-code";
  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-start gap-3 px-4 py-4 [@media(min-height:600px)]:justify-center [@media(min-height:600px)]:gap-6 [@media(min-height:600px)]:py-10">
      <h1 className="text-2xl font-semibold text-zinc-50">Two-step verification</h1>
      <form onSubmit={onSubmit} className="space-y-4 rounded-xl border border-zinc-800 bg-zinc-900/70 p-5">
        <p className="text-sm text-zinc-400">
          {useRecovery
            ? "Enter one of your recovery codes. Each code works once."
            : "Enter the 6-digit code from your authenticator app."}
        </p>
        <div className="space-y-2">
          <Label htmlFor={inputId}>{useRecovery ? "Recovery code" : "Authentication code"}</Label>
          <Input
            key={inputId}
            id={inputId}
            type="text"
            inputMode={useRecovery ? "text" : "numeric"}
            autoComplete="one-time-code"
            pattern={useRecovery ? undefined : "[0-9 ]{6,7}"}
            maxLength={useRecovery ? 32 : 7}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            aria-invalid={error ? true : undefined}
            aria-describedby={error ? CODE_ERROR_ID : undefined}
            autoFocus
            required
          />
        </div>
        <label className="flex items-center gap-2 text-sm text-zinc-300">
          <input
            type="checkbox"
            checked={remember}
            onChange={(e) => setRemember(e.target.checked)}
            className="h-4 w-4 accent-emerald-500"
          />
          Remember this device for 30 days
        </label>
        {error ? <ErrorState id={CODE_ERROR_ID} message={error} /> : null}
        <Button type="submit" disabled={busy} className="w-full">
          Verify
        </Button>
        <div className="flex items-center justify-between text-xs">
          <button
            type="button"
            className="text-emerald-400 hover:underline"
            onClick={() => {
              setUseRecovery((current) => !current);
              setValue("");
              setError(null);
            }}
          >
            {useRecovery ? "Use authenticator code" : "Use a recovery code"}
          </button>
          <button type="button" className="text-zinc-400 hover:underline" onClick={onBack}>
            Back
          </button>
        </div>
      </form>
    </div>
  );
}
