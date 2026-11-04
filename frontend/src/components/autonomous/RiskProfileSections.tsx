"use client";

import { useCallback } from "react";

import { GradeRiskCard } from "@/components/autonomous/GradeRiskCard";
import { LeverageSection } from "@/components/autonomous/LeverageSection";
import { LeverageSettingsSlot } from "@/components/autonomous/LeverageSettingsSlot";
import { LoadingState } from "@/components/states";
import { useAsyncData } from "@/hooks/useAsyncData";
import { api } from "@/lib/api";
import type { AutonomousTradingSettingsView, RiskPolicyView } from "@/lib/api/types";

import type { Bounds } from "./bounds";
import { PROFILE_KINDS } from "./profile-labels";

type Props = {
  /**
   * The settings the page already loaded, used to detect PR #430's leverage
   * fields. Omitted: the leverage slot renders on its own (previous behaviour).
   */
  settings?: AutonomousTradingSettingsView | null;
  bounds?: Bounds;
};

/** Loads the paper and demo risk profiles once for grade risk and the leverage slot. */
export function RiskProfileSections({ settings, bounds = null }: Props = {}) {
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
      {settings === undefined ? (
        <LeverageSettingsSlot profiles={profiles} />
      ) : (
        <LeverageSection profiles={profiles} settings={settings} bounds={bounds} />
      )}
    </>
  );
}
