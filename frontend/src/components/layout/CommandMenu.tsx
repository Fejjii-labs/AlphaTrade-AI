"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";

import { ADVANCED_ROUTES, LABELED_ROUTES } from "@/components/layout/advanced-routes";
import {
  PRIMARY_DESTINATIONS,
  SECONDARY_NAV,
  type NavLink,
} from "@/components/layout/navigation-config";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { cn } from "@/lib/utils";

type CommandMenuProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

type CommandEntry = {
  href: string;
  label: string;
  group: string;
};

function buildEntries(): CommandEntry[] {
  const entries: CommandEntry[] = PRIMARY_DESTINATIONS.map((d) => ({
    href: d.href,
    label: d.label,
    group: "Destinations",
  }));
  for (const group of SECONDARY_NAV) {
    for (const item of group.items as readonly NavLink[]) {
      if (entries.some((e) => e.href === item.href)) continue;
      entries.push({
        href: item.href,
        label: item.label,
        group: item.advanced ? "Advanced" : "Pages",
      });
    }
  }
  for (const route of LABELED_ROUTES) {
    if (entries.some((entry) => entry.href === route.href)) continue;
    if (ADVANCED_ROUTES.some((advanced) => advanced.href === route.href)) continue;
    entries.push({ href: route.href, label: route.label, group: "Pages" });
  }
  for (const route of ADVANCED_ROUTES) {
    if (entries.some((entry) => entry.href === route.href)) continue;
    entries.push({
      href: route.href,
      label: route.label,
      group: "Diagnostics",
    });
  }
  return entries;
}

export function CommandMenu({ open, onOpenChange }: CommandMenuProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const optionRefs = useRef<(HTMLAnchorElement | null)[]>([]);
  const listboxId = useId();
  const close = useCallback(() => onOpenChange(false), [onOpenChange]);
  useFocusTrap(panelRef, open, close);

  const entries = useMemo(() => buildEntries(), []);
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(
      (entry) => entry.label.toLowerCase().includes(q) || entry.href.toLowerCase().includes(q),
    );
  }, [entries, query]);

  // Filtering can shrink the list underneath the cursor.
  const boundedIndex = filtered.length ? Math.min(activeIndex, filtered.length - 1) : -1;
  const activeOptionId = boundedIndex >= 0 ? `${listboxId}-option-${boundedIndex}` : undefined;

  useEffect(() => {
    if (!open) {
      setQuery("");
      setActiveIndex(0);
      return;
    }
    const timer = window.setTimeout(() => inputRef.current?.focus(), 0);
    return () => window.clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    const option = optionRefs.current[boundedIndex];
    // jsdom does not implement scrollIntoView.
    option?.scrollIntoView?.({ block: "nearest" });
  }, [boundedIndex]);

  /**
   * Listbox keyboard control. Focus stays on the input and the active option is
   * advertised with `aria-activedescendant`, so typing and navigating never
   * fight over focus.
   */
  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (!filtered.length) return;
    const last = filtered.length - 1;
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setActiveIndex(boundedIndex >= last ? 0 : boundedIndex + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        setActiveIndex(boundedIndex <= 0 ? last : boundedIndex - 1);
        break;
      case "Home":
        event.preventDefault();
        setActiveIndex(0);
        break;
      case "End":
        event.preventDefault();
        setActiveIndex(last);
        break;
      case "Enter":
        event.preventDefault();
        optionRefs.current[boundedIndex]?.click();
        break;
      default:
        break;
    }
  };

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpenChange(!open);
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[60]" data-testid="command-menu" role="presentation">
      <button
        type="button"
        className="absolute inset-0 bg-black/60"
        aria-label="Close command menu"
        onClick={close}
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label="Command menu"
        className={cn(
          "relative mx-auto mt-[12vh] w-[min(32rem,calc(100%-2rem))] overflow-hidden rounded-card",
          "border border-border-subtle bg-surface-0 shadow-lg",
        )}
      >
        <div className="border-b border-border-subtle p-3">
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={onInputKeyDown}
            placeholder="Jump to a destination or page…"
            aria-label="Filter command menu"
            role="combobox"
            aria-expanded="true"
            aria-autocomplete="list"
            aria-controls={listboxId}
            aria-activedescendant={activeOptionId}
            className="w-full rounded-control border border-border-subtle bg-surface-1 px-3 py-2 text-sm text-text-primary outline-none focus-visible:ring-2 focus-visible:ring-focus"
          />
        </div>
        <ul
          id={listboxId}
          role="listbox"
          className="max-h-80 overflow-y-auto p-2"
          aria-label="Command results"
        >
          {filtered.length === 0 ? (
            <li className="px-3 py-4 text-sm text-text-muted">No matches</li>
          ) : (
            filtered.map((entry, index) => {
              const active = index === boundedIndex;
              return (
                <li key={`${entry.group}-${entry.href}`} role="presentation">
                  <Link
                    ref={(node) => {
                      optionRefs.current[index] = node;
                    }}
                    id={`${listboxId}-option-${index}`}
                    role="option"
                    aria-selected={active}
                    tabIndex={-1}
                    href={entry.href}
                    onClick={close}
                    onMouseEnter={() => setActiveIndex(index)}
                    className={cn(
                      "flex min-h-11 items-center justify-between rounded-control px-3 py-2.5 text-sm",
                      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
                      active ? "bg-surface-2" : "hover:bg-surface-1",
                    )}
                  >
                    <span className="text-text-primary">{entry.label}</span>
                    <span className="text-caption text-text-muted">{entry.group}</span>
                  </Link>
                </li>
              );
            })
          )}
        </ul>
      </div>
    </div>
  );
}
