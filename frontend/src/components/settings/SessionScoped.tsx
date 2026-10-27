"use client";

import { useEffect, useState } from "react";

import { useAuth } from "@/contexts/AuthContext";
import { onSessionCleared, sessionGeneration } from "@/lib/auth/session-events";

/**
 * Remounts its children when the signed-in user, workspace or session changes,
 * so no state from the previous account survives. Renders nothing signed out.
 */
export function SessionScoped({ children }: { children: React.ReactNode }) {
  const { user, organization } = useAuth();
  const [session, setSession] = useState(sessionGeneration);
  useEffect(() => onSessionCleared(() => setSession(sessionGeneration())), []);
  if (!user || !organization) return null;
  return <div key={JSON.stringify([organization.id, user.id, session])}>{children}</div>;
}
