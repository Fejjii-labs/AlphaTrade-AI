"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

const chipClass =
  "inline-flex min-h-9 items-center gap-1.5 rounded-pill border px-3 py-1 text-[0.8125rem] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus disabled:cursor-not-allowed disabled:opacity-50";

export interface ChipProps extends Omit<React.ComponentProps<"button">, "children"> {
  label: string;
  count?: number;
  pressed?: boolean;
  onPressedChange?: (pressed: boolean) => void;
}

/** Toggle chip. Counts stay visible text, not colour alone. */
export function Chip({
  label,
  count,
  pressed = false,
  onPressedChange,
  className,
  disabled,
  onClick,
  ref,
  ...props
}: ChipProps) {
  return (
    <button
      ref={ref}
      type="button"
      aria-pressed={pressed}
      disabled={disabled}
      data-testid="chip"
      aria-label={count != null ? `${label} ${count}` : undefined}
      className={cn(
        chipClass,
        pressed
          ? "border-accent-border bg-accent-muted text-accent"
          : "border-border-subtle bg-surface-1 text-text-secondary hover:border-border hover:text-text-primary",
        className,
      )}
      onClick={(event) => {
        onClick?.(event);
        if (!event.defaultPrevented) onPressedChange?.(!pressed);
      }}
      {...props}
    >
      <span>{label}</span>
      {count != null ? (
        <span className="font-data text-[length:var(--text-caption)] leading-[1.4] text-text-muted">{count}</span>
      ) : null}
    </button>
  );
}

export interface FilterChipOption {
  id: string;
  label: string;
  count?: number;
  disabled?: boolean;
}

export interface FilterChipsProps {
  options: FilterChipOption[];
  value: string[];
  onChange: (next: string[]) => void;
  selection?: "single" | "multiple";
  "aria-label": string;
  className?: string;
}

function enabledOptions(options: FilterChipOption[]): FilterChipOption[] {
  return options.filter((option) => !option.disabled);
}

function nextEnabledId(options: FilterChipOption[], currentId: string, delta: number): string | null {
  const enabled = enabledOptions(options);
  if (enabled.length === 0) return null;
  const index = enabled.findIndex((option) => option.id === currentId);
  const base = index >= 0 ? index : 0;
  const next = (base + delta + enabled.length) % enabled.length;
  return enabled[next]?.id ?? null;
}

/** Filter row with counts. Arrows move focus; Enter and Space toggle. */
export function FilterChips({
  options,
  value,
  onChange,
  selection = "multiple",
  "aria-label": ariaLabel,
  className,
}: FilterChipsProps) {
  const [focusedId, setFocusedId] = React.useState<string | null>(null);
  const refs = React.useRef(new Map<string, HTMLButtonElement>());
  const activatedByKey = React.useRef(false);
  const enabled = enabledOptions(options);
  const tabStopId =
    focusedId && enabled.some((option) => option.id === focusedId)
      ? focusedId
      : (enabled[0]?.id ?? null);

  const toggle = (id: string) => {
    const selected = value.includes(id);
    if (selection === "single") {
      onChange(selected ? [] : [id]);
      return;
    }
    onChange(selected ? value.filter((item) => item !== id) : [...value, id]);
  };

  const focusChip = (id: string) => {
    setFocusedId(id);
    refs.current.get(id)?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!tabStopId) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activatedByKey.current = true;
      toggle(tabStopId);
      queueMicrotask(() => {
        activatedByKey.current = false;
      });
      return;
    }
    let nextId: string | null = null;
    switch (event.key) {
      case "ArrowRight":
      case "ArrowDown":
        event.preventDefault();
        nextId = nextEnabledId(options, tabStopId, 1);
        break;
      case "ArrowLeft":
      case "ArrowUp":
        event.preventDefault();
        nextId = nextEnabledId(options, tabStopId, -1);
        break;
      case "Home":
        event.preventDefault();
        nextId = enabled[0]?.id ?? null;
        break;
      case "End":
        event.preventDefault();
        nextId = enabled[enabled.length - 1]?.id ?? null;
        break;
      default:
        return;
    }
    if (nextId) focusChip(nextId);
  };

  return (
    <div
      role="toolbar"
      aria-label={ariaLabel}
      data-testid="filter-chips"
      onKeyDown={onKeyDown}
      className={cn("flex flex-wrap gap-2", className)}
    >
      {options.map((option) => {
        const pressed = value.includes(option.id);
        const tabStop = !option.disabled && option.id === tabStopId;
        return (
          <Chip
            key={option.id}
            label={option.label}
            count={option.count}
            pressed={pressed}
            disabled={option.disabled}
            tabIndex={tabStop ? 0 : -1}
            ref={(node) => {
              if (node) refs.current.set(option.id, node);
              else refs.current.delete(option.id);
            }}
            onFocus={() => {
              if (!option.disabled) setFocusedId(option.id);
            }}
            onClick={(event) => {
              event.preventDefault();
              if (activatedByKey.current || option.disabled) return;
              setFocusedId(option.id);
              toggle(option.id);
            }}
          />
        );
      })}
    </div>
  );
}
