"use client";

import { createPortal } from "react-dom";

import { useId, useRef, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useFocusTrap } from "@/hooks/useFocusTrap";

/**
 * Modal confirmation for consequential settings changes (arming the paper bot,
 * raising leverage, resetting defaults). Same pattern as the kill switch
 * dialog: portal, backdrop cancel, focus trap with Escape, focus restored to
 * the trigger on close. Nothing is sent until the confirm button is pressed.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  children,
  confirmLabel,
  confirmVariant = "default",
  busy = false,
  error,
  onConfirm,
  onCancel,
  testId = "confirm-dialog",
}: {
  open: boolean;
  title: string;
  description: ReactNode;
  children?: ReactNode;
  confirmLabel: string;
  confirmVariant?: "default" | "destructive";
  busy?: boolean;
  error?: string | null;
  onConfirm: () => void;
  onCancel: () => void;
  testId?: string;
}) {
  const dialogRef = useRef<HTMLDivElement>(null);
  const id = useId();
  useFocusTrap(dialogRef, open, busy ? undefined : onCancel);
  if (!open || typeof document === "undefined") return null;
  return createPortal(
    <div className="fixed inset-0 z-[70] flex items-center justify-center p-4">
      <button
        type="button"
        aria-label={`Cancel: ${title}`}
        tabIndex={-1}
        className="absolute inset-0 bg-black/60"
        onClick={busy ? undefined : onCancel}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-description`}
        data-testid={testId}
        className="relative max-h-[calc(100dvh-2rem)] w-[min(28rem,100%)] space-y-4 overflow-y-auto rounded-card border border-border-subtle bg-surface-0 p-5 text-left shadow-lg"
      >
        <div>
          <h2 id={`${id}-title`} className="text-base font-semibold text-text-primary">
            {title}
          </h2>
          <div id={`${id}-description`} className="mt-2 text-sm text-text-secondary">
            {description}
          </div>
        </div>
        {children}
        {error ? (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        ) : null}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onCancel} disabled={busy} data-testid={`${testId}-cancel`}>
            Cancel
          </Button>
          <Button
            variant={confirmVariant}
            onClick={onConfirm}
            disabled={busy}
            data-testid={`${testId}-confirm`}
          >
            {busy ? "Saving…" : confirmLabel}
          </Button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
