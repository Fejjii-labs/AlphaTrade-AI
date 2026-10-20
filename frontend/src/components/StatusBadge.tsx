import { Badge, type BadgeProps } from "@/components/ui/badge";

const toneMap: Record<string, BadgeProps["variant"]> = {
  ok: "success",
  healthy: "success",
  success: "success",
  paper: "paper",
  pending: "warning",
  degraded: "warning",
  warn: "warning",
  warning: "warning",
  blocked: "blocked",
  unavailable: "danger",
  critical: "danger",
  rejected: "danger",
  stale: "stale",
  info: "info",
  muted: "muted",
};

export function StatusBadge({
  label,
  tone = "default",
  className,
}: {
  label: string;
  tone?: keyof typeof toneMap | "default";
  className?: string;
}) {
  const variant = tone === "default" ? "default" : (toneMap[tone] ?? "default");
  return <Badge variant={variant} className={className}>{label}</Badge>;
}
