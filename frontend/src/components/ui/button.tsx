import { cva, type VariantProps } from "class-variance-authority";
import * as React from "react";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-control text-[0.8125rem] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-accent-solid text-white hover:opacity-90",
        secondary:
          "border border-border bg-surface-1 text-text-primary hover:bg-surface-2",
        outline:
          "border border-border bg-transparent text-text-primary hover:bg-surface-1",
        ghost: "text-text-secondary hover:bg-surface-1 hover:text-text-primary",
        destructive: "bg-danger text-text-inverse hover:opacity-90",
        warning: "bg-warning text-text-inverse hover:opacity-90",
      },
      size: {
        default: "h-10 px-4 py-2",
        sm: "h-8 px-3 text-[length:var(--text-caption)]",
        lg: "h-11 px-6",
        // Icon-only controls carry no text hit area, so they hold the 44 px
        // minimum touch target on their own.
        icon: "h-11 w-11",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ComponentProps<"button">,
    VariantProps<typeof buttonVariants> {}

export function Button({ className, variant, size, ...props }: ButtonProps) {
  return <button className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}

export { buttonVariants };
