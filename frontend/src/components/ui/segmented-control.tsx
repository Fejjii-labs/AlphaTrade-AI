"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export type SegmentedTone = "default" | "paper" | "demo";

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
  disabled?: boolean;
  /** Selected fill. Paper and demo match the mode pill. */
  tone?: SegmentedTone;
}

const selectedTone: Record<SegmentedTone, string> = {
  default: "bg-surface-raised text-text-primary",
  paper: "bg-mode-paper-muted text-mode-paper",
  demo: "bg-mode-demo-muted text-mode-demo",
};

export interface SegmentedControlProps<T extends string> {
  value: T;
  onChange: (next: T) => void;
  options: Array<SegmentedOption<T>>;
  "aria-label": string;
  className?: string;
}

function enabledOptions<T extends string>(options: Array<SegmentedOption<T>>) {
  return options.filter((option) => !option.disabled);
}

function nextEnabled<T extends string>(
  options: Array<SegmentedOption<T>>,
  current: T,
  delta: number,
): T | null {
  const enabled = enabledOptions(options);
  if (enabled.length === 0) return null;
  const index = enabled.findIndex((option) => option.value === current);
  const base = index >= 0 ? index : 0;
  const next = (base + delta + enabled.length) % enabled.length;
  return enabled[next]?.value ?? null;
}

/**
 * Single-choice control (7D/30D, Demo/Simulator, Journal/Knowledge).
 * Arrow keys move and select. Disabled options are skipped.
 */
export function SegmentedControl<T extends string>({
  value,
  onChange,
  options,
  "aria-label": ariaLabel,
  className,
}: SegmentedControlProps<T>) {
  const refs = React.useRef(new Map<string, HTMLButtonElement>());
  const enabled = enabledOptions(options);
  const tabStop =
    enabled.some((option) => option.value === value) ? value : (enabled[0]?.value ?? null);

  const select = (next: T) => {
    const option = options.find((item) => item.value === next);
    if (!option || option.disabled) return;
    onChange(next);
    refs.current.get(next)?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (enabled.length === 0 || tabStop == null) return;
    let next: T | null = null;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        event.preventDefault();
        next = nextEnabled(options, tabStop, 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        event.preventDefault();
        next = nextEnabled(options, tabStop, -1);
        break;
      case "Home":
        event.preventDefault();
        next = enabled[0]?.value ?? null;
        break;
      case "End":
        event.preventDefault();
        next = enabled[enabled.length - 1]?.value ?? null;
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        select(tabStop);
        return;
      default:
        return;
    }
    if (next) select(next);
  };

  return (
    <div
      role="radiogroup"
      aria-label={ariaLabel}
      data-testid="segmented-control"
      onKeyDown={onKeyDown}
      className={cn(
        "inline-flex flex-wrap gap-1 rounded-control border border-border-subtle bg-surface-2 p-1",
        className,
      )}
    >
      {options.map((option) => {
        const selected = option.value === value;
        const isTabStop = !option.disabled && option.value === tabStop;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={selected}
            disabled={option.disabled}
            tabIndex={isTabStop ? 0 : -1}
            ref={(node) => {
              if (node) refs.current.set(option.value, node);
              else refs.current.delete(option.value);
            }}
            className={cn(
              "min-h-9 rounded-[calc(var(--radius-control)-2px)] px-3 py-1 text-[0.8125rem] font-medium",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
              "disabled:cursor-not-allowed disabled:opacity-50",
              selected
                ? selectedTone[option.tone ?? "default"]
                : "text-text-muted hover:text-text-secondary",
            )}
            onClick={() => select(option.value)}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
