"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

export interface TabItem {
  id: string;
  label: string;
  disabled?: boolean;
}

type TabsContextValue = {
  idPrefix: string;
  value: string;
  onChange: (id: string) => void;
};

const TabsContext = React.createContext<TabsContextValue | null>(null);

function useTabsContext(component: string): TabsContextValue {
  const ctx = React.useContext(TabsContext);
  if (!ctx) {
    throw new Error(`${component} must be used within TabsRoot`);
  }
  return ctx;
}

export interface TabsRootProps {
  children: React.ReactNode;
  value: string;
  onChange: (id: string) => void;
  /**
   * Optional stable prefix for tab/panel ids.
   * When omitted, TabsRoot owns a single React.useId() shared by all descendants.
   */
  idPrefix?: string;
}

/** Owns one collision-safe id prefix for a Tabs + TabPanel group. */
export function TabsRoot({ children, value, onChange, idPrefix }: TabsRootProps) {
  const generatedPrefix = React.useId();
  const prefix = idPrefix ?? generatedPrefix;
  const contextValue = React.useMemo(
    () => ({ idPrefix: prefix, value, onChange }),
    [prefix, value, onChange],
  );
  return <TabsContext.Provider value={contextValue}>{children}</TabsContext.Provider>;
}

export interface TabsProps {
  items: TabItem[];
  onIntent?: (id: string) => void;
  className?: string;
  "aria-label"?: string;
}

function enabledItems(items: TabItem[]): TabItem[] {
  return items.filter((item) => !item.disabled);
}

function nextEnabledId(items: TabItem[], currentId: string, delta: number): string | null {
  const enabled = enabledItems(items);
  if (enabled.length === 0) return null;
  const idx = enabled.findIndex((item) => item.id === currentId);
  const base = idx >= 0 ? idx : 0;
  const next = (base + delta + enabled.length) % enabled.length;
  return enabled[next]?.id ?? null;
}

/** Resolve the single enabled tab that should receive tabIndex={0}. */
function resolveRovingTabId(
  items: TabItem[],
  preferredId: string | null,
  selectedId: string,
): string | null {
  const enabled = enabledItems(items);
  if (enabled.length === 0) return null;
  if (preferredId && enabled.some((item) => item.id === preferredId)) {
    return preferredId;
  }
  if (enabled.some((item) => item.id === selectedId)) {
    return selectedId;
  }
  return enabled[0]?.id ?? null;
}

/** Lightweight accessible tabs (no Radix dependency). Manual activation + arrow keys. */
export function Tabs({ items, onIntent, className, "aria-label": ariaLabel }: TabsProps) {
  const { idPrefix: prefix, value, onChange } = useTabsContext("Tabs");
  const [focusedId, setFocusedId] = React.useState<string | null>(value);
  const tabRefs = React.useRef(new Map<string, HTMLButtonElement>());

  const tabStopId = resolveRovingTabId(items, focusedId, value);

  React.useEffect(() => {
    setFocusedId((current) => resolveRovingTabId(items, current, value));
  }, [items, value]);

  const focusTab = (id: string) => {
    setFocusedId(id);
    tabRefs.current.get(id)?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const enabled = enabledItems(items);
    if (enabled.length === 0) return;

    const current = tabStopId ?? enabled[0].id;
    let nextId: string | null = null;

    switch (event.key) {
      case "ArrowRight":
        event.preventDefault();
        nextId = nextEnabledId(items, current, 1);
        break;
      case "ArrowLeft":
        event.preventDefault();
        nextId = nextEnabledId(items, current, -1);
        break;
      case "Home":
        event.preventDefault();
        nextId = enabled[0]?.id ?? null;
        break;
      case "End":
        event.preventDefault();
        nextId = enabled[enabled.length - 1]?.id ?? null;
        break;
      case "Enter":
      case " ":
        event.preventDefault();
        if (!items.find((item) => item.id === current)?.disabled) {
          onChange(current);
        }
        return;
      default:
        return;
    }

    if (nextId) focusTab(nextId);
  };

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      data-tabs-prefix={prefix}
      onKeyDown={onKeyDown}
      className={cn(
        "inline-flex flex-wrap gap-1 rounded-control border border-border-subtle bg-surface-0 p-1",
        className,
      )}
    >
      {items.map((item) => {
        const selected = item.id === value;
        const tabDomId = `${prefix}-tab-${item.id}`;
        const panelDomId = `${prefix}-tabpanel-${item.id}`;
        const isTabStop = !item.disabled && tabStopId === item.id;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={selected}
            aria-controls={panelDomId}
            disabled={item.disabled}
            id={tabDomId}
            tabIndex={isTabStop ? 0 : -1}
            ref={(node) => {
              if (node) tabRefs.current.set(item.id, node);
              else tabRefs.current.delete(item.id);
            }}
            className={cn(
              "rounded-[calc(var(--radius-control)-2px)] px-3 py-1.5 text-sm font-medium transition-colors",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-focus",
              "disabled:cursor-not-allowed disabled:opacity-50",
              selected
                ? "bg-surface-2 text-text-primary"
                : "text-text-muted hover:text-text-secondary",
            )}
            onClick={() => {
              if (item.disabled) return;
              setFocusedId(item.id);
              onChange(item.id);
            }}
            onFocus={() => {
              if (!item.disabled) { setFocusedId(item.id); onIntent?.(item.id); }
            }}
            onMouseEnter={() => { if (!item.disabled) onIntent?.(item.id); }}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  id,
  children,
  className,
}: {
  id: string;
  children: React.ReactNode;
  className?: string;
}) {
  const { idPrefix: prefix, value: activeId } = useTabsContext("TabPanel");
  const inactive = id !== activeId;
  return (
    <div
      role="tabpanel"
      id={`${prefix}-tabpanel-${id}`}
      aria-labelledby={`${prefix}-tab-${id}`}
      hidden={inactive}
      className={className}
    >
      {children}
    </div>
  );
}
