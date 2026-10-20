import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-pill border px-2.5 py-0.5 text-[length:var(--text-caption)] font-medium leading-[1.4]",
  {
    variants: {
      variant: {
        default: "border-border bg-surface-2 text-text-primary",
        success: "border-success-border bg-success-muted text-success",
        warning: "border-warning-border bg-warning-muted text-warning",
        danger: "border-danger-border bg-danger-muted text-danger",
        info: "border-info-border bg-info-muted text-info",
        muted: "border-border-subtle bg-surface-0 text-text-muted",
        paper: "border-mode-paper-border bg-mode-paper-muted text-mode-paper",
        demo: "border-mode-demo-border bg-mode-demo-muted text-mode-demo",
        count: "border-border-subtle bg-surface-2 font-data text-text-secondary",
        blocked: "border-blocked-border bg-blocked-muted text-blocked",
        stale: "border-stale-border bg-stale-muted text-stale",
        riskInfo:
          "border-[color:color-mix(in_srgb,var(--color-risk-info)_35%,transparent)] bg-[color:color-mix(in_srgb,var(--color-risk-info)_12%,transparent)] text-risk-info",
        riskLow:
          "border-[color:color-mix(in_srgb,var(--color-risk-low)_35%,transparent)] bg-[color:color-mix(in_srgb,var(--color-risk-low)_12%,transparent)] text-risk-low",
        riskMedium:
          "border-[color:color-mix(in_srgb,var(--color-risk-medium)_35%,transparent)] bg-[color:color-mix(in_srgb,var(--color-risk-medium)_12%,transparent)] text-risk-medium",
        riskHigh:
          "border-[color:color-mix(in_srgb,var(--color-risk-high)_35%,transparent)] bg-[color:color-mix(in_srgb,var(--color-risk-high)_12%,transparent)] text-risk-high",
        riskCritical: "border-transparent bg-risk-critical text-text-inverse",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant, className }))} {...props} />;
}

export { badgeVariants };
