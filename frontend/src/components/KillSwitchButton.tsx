"use client";

import { createPortal } from "react-dom";

import { OctagonAlert } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { FieldError, Input, Label } from "@/components/ui/input";
import { useAppContext } from "@/contexts/AppContext";
import { useFocusTrap } from "@/hooks/useFocusTrap";

const MIN_REASON_LENGTH = 3;

export function KillSwitchButton({ compact = false }: { compact?: boolean }) {
  const {
    killSwitchActive,
    killSwitchBusy,
    killSwitchError,
    setKillSwitchActive,
    refreshKillSwitch,
  } = useAppContext();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const reasonRef = useRef<HTMLInputElement>(null);
  const dialogId = useId();
  const reasonErrorId = `${dialogId}-reason-error`;

  const nextActive = !killSwitchActive;
  const actionLabel = nextActive ? "Activate" : "Deactivate";

  const closeDialog = useCallback(() => {
    setConfirmOpen(false);
    setLocalError(null);
    triggerRef.current?.focus();
  }, []);

  useFocusTrap(dialogRef, confirmOpen, closeDialog);

  useEffect(() => {
    if (!confirmOpen) return;
    setReason("");
    const timer = window.setTimeout(() => reasonRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [confirmOpen]);

  async function handleConfirm() {
    const trimmed = reason.trim();
    if (trimmed.length < MIN_REASON_LENGTH) {
      setLocalError(
        `A reason of at least ${MIN_REASON_LENGTH} characters is required.`,
      );
      reasonRef.current?.focus();
      return;
    }
    setLocalError(null);
    try {
      await setKillSwitchActive(nextActive, trimmed);
      closeDialog();
    } catch {
      setLocalError(killSwitchError ?? "Couldn't update. Try again.");
    }
  }

  return (
    <div className="relative flex flex-col items-end gap-1">
      <Button
        ref={triggerRef}
        variant={killSwitchActive ? "destructive" : "outline"}
        size={compact ? "sm" : "default"}
        className="min-h-11"
        onClick={() => setConfirmOpen(true)}
        disabled={killSwitchBusy}
        aria-pressed={killSwitchActive}
        aria-haspopup="dialog"
        aria-expanded={confirmOpen}
        data-testid="kill-switch-button"
      >
        <OctagonAlert className="h-4 w-4" aria-hidden="true" />
        {killSwitchBusy
          ? "Updating…"
          : killSwitchActive
            ? "Paused"
            : "Pause"}
      </Button>

      {confirmOpen
        ? createPortal(
            <div className="fixed inset-0 z-[70] flex items-center justify-center p-4">
              <button
                type="button"
                aria-label="Cancel kill switch change"
                className="absolute inset-0 bg-black/60"
                onClick={closeDialog}
              />
              <div
                ref={dialogRef}
                role="dialog"
                aria-modal="true"
                aria-labelledby={`${dialogId}-title`}
                aria-describedby={`${dialogId}-description`}
                data-testid="kill-switch-confirm"
                className="relative max-h-[calc(100dvh-2rem)] overflow-y-auto w-[min(28rem,100%)] space-y-4 rounded-card border border-border-subtle bg-surface-0 p-5 text-left shadow-lg"
              >
                <div>
                  <h2
                    id={`${dialogId}-title`}
                    className="text-base font-semibold text-text-primary"
                  >
                    {actionLabel} organization kill switch?
                  </h2>
                  <p
                    id={`${dialogId}-description`}
                    className="mt-2 text-sm text-text-secondary"
                  >
                    {nextActive
                      ? "The bot stops opening new trades."
                      : "The bot may open new trades again."}
                  </p>
                </div>

                <div className="space-y-2">
                  <Label htmlFor={`${dialogId}-reason`}>
                    Reason (required)
                  </Label>
                  <Input
                    ref={reasonRef}
                    id={`${dialogId}-reason`}
                    value={reason}
                    onChange={(event) => setReason(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter") return;
                      event.preventDefault();
                      void handleConfirm();
                    }}
                    aria-invalid={localError ? true : undefined}
                    aria-describedby={localError ? reasonErrorId : undefined}
                    data-testid="kill-switch-reason"
                    placeholder={
                      nextActive ? "Emergency halt" : "Resume paper trading"
                    }
                  />
                  <FieldError id={reasonErrorId} message={localError} />
                </div>

                <div className="flex justify-end gap-2">
                  <Button
                    variant="outline"
                    onClick={closeDialog}
                    data-testid="kill-switch-cancel"
                  >
                    Cancel
                  </Button>
                  <Button
                    variant={nextActive ? "destructive" : "default"}
                    onClick={() => void handleConfirm()}
                    disabled={killSwitchBusy}
                    data-testid="kill-switch-confirm-action"
                  >
                    {killSwitchBusy
                      ? "Updating…"
                      : `${actionLabel} kill switch`}
                  </Button>
                </div>
              </div>
            </div>,
            document.body,
          )
        : null}

      {killSwitchError && !confirmOpen ? (
        <span
          className="flex max-w-[16rem] items-center gap-1 text-right text-xs text-danger"
          role="alert"
        >
          {killSwitchError}
          <button
            type="button"
            className="underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus"
            onClick={() => void refreshKillSwitch()}
          >
            Retry
          </button>
        </span>
      ) : null}
    </div>
  );
}
