import { Badge } from "@/components/ui/badge";
import type { RiskSeverity } from "@/lib/api/types";

const variantMap: Record<
  RiskSeverity,
  "riskInfo" | "riskLow" | "riskMedium" | "riskHigh" | "riskCritical"
> = {
  info: "riskInfo",
  low: "riskLow",
  medium: "riskMedium",
  high: "riskHigh",
  critical: "riskCritical",
};

export function RiskBadge({ level }: { level: RiskSeverity | string | null | undefined }) {
  if (!level) return <Badge variant="muted">Risk unknown</Badge>;
  const normalized = level.toLowerCase() as RiskSeverity;
  return <Badge variant={variantMap[normalized] ?? "default"}>Risk {level}</Badge>;
}
