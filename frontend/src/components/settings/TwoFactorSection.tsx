"use client";

import { useCallback, useEffect, useState } from "react";

import { ErrorState, SuccessState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Row } from "@/components/ui/row";
import { SettingsUnavailable } from "@/components/settings/SettingsReadout";
import { api } from "@/lib/api";
import { friendlyError } from "@/lib/api/client";
import type { TwoFactorSetup, TwoFactorStatus } from "@/lib/api/types";
import { setTokens } from "@/lib/auth/session";

/** start: password before a secret is shown; setup: QR + first code; disable: password + factor. */
type Mode = "idle" | "start" | "setup" | "disable" | "regenerate";

/** Settings > Account: turn two-factor sign-in (authenticator app) on or off. */
export function TwoFactorSection() {
  const [status, setStatus] = useState<TwoFactorStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<Mode>("idle");
  const [setup, setSetup] = useState<TwoFactorSetup | null>(null);
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await api.auth.twoFactor.status());
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  function reset(next: Mode = "idle") {
    setMode(next);
    setCode("");
    setPassword("");
    setUseRecovery(false);
    setError(null);
  }

  async function run(action: () => Promise<void>, fallback: string) {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await action();
    } catch (err) {
      setError(friendlyError(err, fallback));
    } finally {
      setBusy(false);
    }
  }

  const submitCode = (event: React.FormEvent) => {
    event.preventDefault();
    const clean = code.replace(/\s/g, "");
    void run(async () => {
      if (mode === "start") {
        setRecoveryCodes(null);
        setSetup(await api.auth.twoFactor.setup(password));
        reset("setup");
        return;
      }
      if (mode === "setup") {
        const result = await api.auth.twoFactor.confirm(clean);
        // Turning 2FA on signs out every other session; keep this one on the new tokens.
        setTokens(result.tokens.access_token, result.tokens.refresh_token || undefined);
        setSetup(null);
        setRecoveryCodes(result.recovery_codes);
        setNotice(
          result.revoked_sessions > 1
            ? "Two-factor sign-in is on. Your other sessions were signed out."
            : "Two-factor sign-in is on.",
        );
      } else if (mode === "regenerate") {
        const result = await api.auth.twoFactor.regenerateRecoveryCodes(clean);
        setRecoveryCodes(result.recovery_codes);
        setNotice("New recovery codes. The old ones no longer work.");
      } else if (mode === "disable") {
        await api.auth.twoFactor.disable(
          password,
          useRecovery ? { recovery_code: code.trim() } : { code: clean },
        );
        setRecoveryCodes(null);
        setNotice("Two-factor sign-in is off.");
      }
      reset();
      await load();
    }, "Couldn't check the code.");
  };

  const forgetDevices = () =>
    run(async () => {
      const result = await api.auth.twoFactor.revokeDevices();
      setNotice(result.message);
      await load();
    }, "Couldn't forget devices.");

  return (
    <Card data-testid="two-factor-settings">
      <CardHeader>
        <CardTitle>Two-factor sign-in</CardTitle>
        <CardDescription>
          Ask for a code from an authenticator app (Google Authenticator, 1Password, Authy…) after
          your password.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!status ? (
          <SettingsUnavailable label="Two-factor status" loading={loading} onRetry={() => void load()} />
        ) : (
          <dl className="divide-y divide-border-subtle">
            <Row definition label="Status" value={status.enabled ? "On" : "Off"} />
            {status.enabled ? (
              <>
                <Row definition label="Recovery codes left" value={String(status.recovery_codes_remaining)} />
                <Row definition label="Remembered devices" value={String(status.remembered_devices)} />
              </>
            ) : null}
          </dl>
        )}

        {notice ? <SuccessState message={notice} /> : null}

        {recoveryCodes ? <RecoveryCodes codes={recoveryCodes} onDone={() => setRecoveryCodes(null)} /> : null}

        {mode === "setup" && setup ? <SetupInstructions setup={setup} /> : null}

        {mode !== "idle" ? (
          <form onSubmit={submitCode} className="space-y-3">
            {mode === "start" || mode === "disable" ? (
              <div className="space-y-2">
                <Label htmlFor="two-factor-password">Current password</Label>
                <Input
                  id="two-factor-password"
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? "two-factor-error" : undefined}
                  required
                />
              </div>
            ) : null}
            {mode !== "start" ? (
              <div className="space-y-2">
                <Label htmlFor="two-factor-code">
                  {mode === "setup"
                    ? "Code from the app"
                    : useRecovery
                      ? "Recovery code"
                      : "Current authenticator code"}
                </Label>
                <Input
                  key={useRecovery ? "recovery" : "totp"}
                  id="two-factor-code"
                  type="text"
                  inputMode={useRecovery ? "text" : "numeric"}
                  autoComplete="one-time-code"
                  pattern={useRecovery ? undefined : "[0-9 ]{6,7}"}
                  maxLength={useRecovery ? 32 : 7}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  aria-invalid={error ? true : undefined}
                  aria-describedby={error ? "two-factor-error" : undefined}
                  required
                />
                {mode === "disable" ? (
                  <button
                    type="button"
                    className="text-xs text-accent hover:underline"
                    onClick={() => {
                      setUseRecovery((current) => !current);
                      setCode("");
                      setError(null);
                    }}
                  >
                    {useRecovery ? "Use authenticator code" : "Lost your phone? Use a recovery code"}
                  </button>
                ) : null}
              </div>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button
                type="submit"
                variant={mode === "disable" ? "destructive" : "default"}
                disabled={busy}
              >
                {mode === "start"
                  ? "Continue"
                  : mode === "setup"
                    ? "Turn on"
                    : mode === "disable"
                      ? "Turn off"
                      : "Get new codes"}
              </Button>
              <Button
                type="button"
                variant="ghost"
                onClick={() => {
                  setSetup(null);
                  reset();
                }}
              >
                Cancel
              </Button>
            </div>
          </form>
        ) : null}

        {error ? <ErrorState id="two-factor-error" message={error} /> : null}

        {status && mode === "idle" ? (
          <div className="flex flex-wrap gap-2">
            {status.enabled ? (
              <>
                <Button variant="outline" disabled={busy} onClick={() => reset("regenerate")}>
                  New recovery codes
                </Button>
                {status.remembered_devices > 0 ? (
                  <Button variant="outline" disabled={busy} onClick={() => void forgetDevices()}>
                    Forget remembered devices
                  </Button>
                ) : null}
                <Button variant="outline" disabled={busy} onClick={() => reset("disable")}>
                  Turn off
                </Button>
              </>
            ) : status.enrolment_available === false ? (
              <p className="text-sm text-text-secondary" data-testid="two-factor-unavailable">
                Two-factor sign-in isn&apos;t available on this server yet.
              </p>
            ) : (
              <Button disabled={busy} onClick={() => reset("start")}>
                Turn on two-factor sign-in
              </Button>
            )}
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}

function SetupInstructions({ setup }: { setup: TwoFactorSetup }) {
  return (
    <div className="space-y-3 text-sm text-text-secondary">
      <p>1. Scan this QR code with your authenticator app.</p>
      {/* eslint-disable-next-line @next/next/no-img-element -- server-rendered SVG data URI */}
      <img
        src={setup.qr_svg_data_uri}
        alt="QR code for your authenticator app"
        width={180}
        height={180}
        className="rounded-md bg-white p-2"
      />
      <p>Or enter this key by hand:</p>
      <code
        data-testid="two-factor-manual-key"
        className="block break-all rounded-md border border-border bg-surface-1 px-3 py-2 font-mono text-text-primary"
      >
        {setup.manual_key.replace(/(.{4})/g, "$1 ").trim()}
      </code>
      <p>2. Enter the {setup.digits}-digit code the app shows.</p>
    </div>
  );
}

function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  return (
    <div className="space-y-3 rounded-card border border-warning-border bg-warning-muted p-4" data-testid="recovery-codes">
      <p className="text-sm text-text-primary">
        Save these recovery codes somewhere safe. Each one signs you in once if you lose your
        phone. They are shown only now.
      </p>
      <ul className="grid grid-cols-2 gap-2 font-mono text-sm text-text-primary">
        {codes.map((value) => (
          <li key={value}>{value}</li>
        ))}
      </ul>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => void navigator.clipboard?.writeText(codes.join("\n"))}
        >
          Copy codes
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={onDone}>
          I saved them
        </Button>
      </div>
    </div>
  );
}
