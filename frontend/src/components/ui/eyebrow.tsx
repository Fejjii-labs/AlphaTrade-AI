import * as React from "react";

import { cn } from "@/lib/utils";

export interface EyebrowProps extends React.HTMLAttributes<HTMLElement> {
  /** `span` when the eyebrow sits inside a heading. */
  as?: "p" | "span";
}

/** Uppercase highlight label above a page or section title. Decoration only. */
export function Eyebrow({ as: Tag = "p", className, ...props }: EyebrowProps) {
  return <Tag className={cn("text-eyebrow", className)} data-testid="eyebrow" {...props} />;
}
