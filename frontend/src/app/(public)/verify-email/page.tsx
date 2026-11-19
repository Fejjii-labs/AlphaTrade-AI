"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { EmailVerificationNotice } from "@/components/account/EmailVerificationNotice";
import { ErrorState, SuccessState } from "@/components/states";
import { useAuth } from "@/contexts/AuthContext";
import { api, ApiError } from "@/lib/api";

function VerifyEmailContent() {
  const params = useSearchParams();
  const token = params.get("token");
  const { user, loading, refreshProfile } = useAuth();
  const [mustVerifyEmail, setMustVerifyEmail] = useState(true);
  const [status, setStatus] = useState<"idle" | "confirming" | "success" | "error">(
    token ? "confirming" : "idle",
  );
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    void api.health
      .get()
      .then((health) => setMustVerifyEmail(health.must_verify_email))
      .catch(() => setMustVerifyEmail(true));
  }, []);

  useEffect(() => {
    if (!token) return;
    void (async () => {
      try {
        const response = await api.auth.confirmVerifyEmail(token);
        setMessage(response.message);
        setStatus("success");
        await refreshProfile();
      } catch (err) {
        setMessage(err instanceof ApiError ? err.message : "Verification failed.");
        setStatus("error");
      }
    })();
  }, [token, refreshProfile]);

  const canContinueWithoutVerification = Boolean(user) && !mustVerifyEmail;

  if (loading) {
    return <p className="text-sm text-zinc-400">Loading…</p>;
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-md flex-col justify-start gap-3 px-4 py-4 [@media(min-height:600px)]:justify-center [@media(min-height:600px)]:gap-6 [@media(min-height:600px)]:py-10">
      <div>
        <h1 className="text-2xl font-semibold text-zinc-50">Email verification</h1>
        <p className="text-sm text-zinc-400">
          {canContinueWithoutVerification
            ? "Staging demo mode — email verification is optional. Continue to the paper-only dashboard."
            : "Confirm your address to unlock staging and production policies."}
        </p>
      </div>
      {status === "confirming" ? <p className="text-sm text-zinc-400">Confirming your link…</p> : null}
      {status === "success" && message ? <SuccessState message={message} /> : null}
      {status === "error" && message ? <ErrorState message={message} /> : null}
      {user && !user.email_verified && mustVerifyEmail ? <EmailVerificationNotice /> : null}
      {user?.email_verified ? (
        <SuccessState message="Your email is verified. You can continue to the workspace." />
      ) : null}
      {canContinueWithoutVerification ? (
        <Link
          href="/"
          className="inline-flex h-11 items-center justify-center rounded-lg bg-emerald-600 px-4 text-sm font-semibold text-white hover:bg-emerald-500"
        >
          Continue to dashboard
        </Link>
      ) : null}
      <Link
        href={user ? "/" : "/login"}
        className="inline-flex h-10 items-center justify-center rounded-lg border border-zinc-700 bg-zinc-800 px-4 text-sm font-medium text-zinc-100 hover:bg-zinc-700"
      >
        {user ? (canContinueWithoutVerification ? "Open dashboard (secondary)" : "Go to dashboard") : "Sign in"}
      </Link>
    </div>
  );
}

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<p className="p-10 text-sm text-zinc-400">Loading…</p>}>
      <VerifyEmailContent />
    </Suspense>
  );
}
