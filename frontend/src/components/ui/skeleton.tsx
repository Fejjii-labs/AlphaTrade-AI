import * as React from "react";

import { cn } from "@/lib/utils";

export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-control bg-surface-2 motion-reduce:animate-none",
        className,
      )}
      aria-hidden="true"
      {...props}
    />
  );
}

export function SkeletonText({
  lines = 3,
  className,
}: {
  lines?: number;
  className?: string;
}) {
  return (
    <div className={cn("space-y-2", className)} aria-hidden="true">
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton
          key={i}
          className={cn("h-3", i === lines - 1 ? "w-[66%]" : "w-full")}
        />
      ))}
    </div>
  );
}

export function StatStripSkeleton({
  cells = 4,
  className,
}: {
  cells?: number;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-3 rounded-card border border-border-subtle bg-surface-1 p-4 sm:grid-cols-4",
        className,
      )}
      role="status"
      aria-label="Loading summary"
      data-testid="stat-strip-skeleton"
    >
      {Array.from({ length: cells }).map((_, index) => (
        <div key={index} className="space-y-2">
          <Skeleton className="h-3 w-16" />
          <Skeleton className="h-8 w-24" />
        </div>
      ))}
    </div>
  );
}

export function TableSkeleton({
  rows = 5,
  columns = 4,
  className,
}: {
  rows?: number;
  columns?: number;
  className?: string;
}) {
  return (
    <div
      role="status"
      aria-label="Loading table"
      data-testid="table-skeleton"
      className={cn("overflow-hidden rounded-card border border-border-subtle", className)}
    >
      <div className="bg-surface-2 px-3 py-2">
        <Skeleton className="h-3 w-full" />
      </div>
      <div className="divide-y divide-border-subtle">
        {Array.from({ length: rows }).map((_, row) => (
          <div
            key={row}
            className="grid gap-3 px-3 py-3"
            style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
          >
            {Array.from({ length: columns }).map((__, column) => (
              <Skeleton key={column} className="h-3 w-full" />
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        "rounded-card border border-border-subtle bg-surface-1 p-4 lg:p-6",
        className,
      )}
      role="status"
      aria-label="Loading"
    >
      <Skeleton className="mb-3 h-4 w-1/3" />
      <SkeletonText lines={3} />
    </div>
  );
}
