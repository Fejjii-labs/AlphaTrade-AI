"use client";

import { useCallback } from "react";

import { GradeRiskCard } from "@/components/autonomous/GradeRiskCard";
import { LeverageSettingsSlot } from "@/components/autonomous/LeverageSettingsSlot";
import { LoadingState } from "@/components/states";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { RiskPolicyView } from "@/lib/api/types";

import { PROFILE_KINDS } from "./profile-labels";

/** Loads the paper and demo risk profiles once for grade risk and the leverage slot. */
export function RiskProfileSections() {
  const loader = useCallback(
    () =>
      Promise.all(PROFILE_KINDS.map((kind) => api.riskPolicy.profile(kind).catch(() => null))).then((rows) =>
        rows.filter((row): row is RiskPolicyView => row !== null),
      ),
    [],
  );
  const { data, loading } = useAsyncData(loader, []);
  if (loading) return <LoadingState label="Loading risk profiles…" />;
  const profiles = data ?? [];
  return (
    <>
      <GradeRiskCard profiles={profiles} />
      <LeverageSettingsSlot profiles={profiles} />
    </>
  );
}
