import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { useState, type ReactElement } from "react";
import { afterEach, describe, expect, it } from "vitest";

import {
  BlockedState,
  EmptyState,
  ErrorState,
  LimitationsState,
  StaleState,
  UnavailableState,
} from "@/components/states";
import { FilterChips } from "@/components/ui/chip";
import { Button } from "@/components/ui/button";
import { ModePill } from "@/components/ui/mode-pill";
import { SegmentedControl } from "@/components/ui/segmented-control";
import { DataNumber } from "@/components/ui/data-number";
import { FieldError, Input } from "@/components/ui/input";
import { FreshnessPill } from "@/components/ui/freshness-pill";
import { IconButton } from "@/components/ui/icon-button";
import { PageHeader } from "@/components/ui/page-header";
import {
  isPaperModeConfirmed,
  PaperModeIndicator,
} from "@/components/ui/paper-mode-indicator";
import { RiskBlock } from "@/components/ui/risk-block";
import { TabPanel, Tabs, TabsRoot } from "@/components/ui/tabs";
import { Tooltip } from "@/components/ui/tooltip";

afterEach(() => cleanup());

function ControlledTabs({
  idPrefix,
  withDisabled = false,
  initialValue = "a",
  items: itemsOverride,
}: {
  idPrefix?: string;
  withDisabled?: boolean;
  initialValue?: string;
  items?: Array<{ id: string; label: string; disabled?: boolean }>;
}): ReactElement {
  const items =
    itemsOverride ??
    (withDisabled
      ? [
          { id: "a", label: "One" },
          { id: "b", label: "Two", disabled: true },
          { id: "c", label: "Three" },
        ]
      : [
          { id: "a", label: "One" },
          { id: "b", label: "Two" },
          { id: "c", label: "Three" },
        ]);
  const panelLabels: Record<string, string> = {
    a: "Panel A",
    b: "Panel B",
    c: "Panel C",
  };
  const [value, setValue] = useState(initialValue);
  return (
    <TabsRoot value={value} onChange={setValue} idPrefix={idPrefix}>
      <Tabs aria-label="Sections" items={items} />
      {items.map((item) => (
        <TabPanel key={item.id} id={item.id}>
          {panelLabels[item.id] ?? `Panel ${item.id}`}
        </TabPanel>
      ))}
    </TabsRoot>
  );
}

function tabStopCount(tabs: HTMLElement[]): number {
  return tabs.filter((tab) => tab.tabIndex === 0 && !(tab as HTMLButtonElement).disabled).length;
}

describe("AT-040 design-system primitives", () => {
  it("renders PageHeader with restrained title hierarchy", () => {
    render(
      <PageHeader
        title="Dashboard"
        description="Paper workspace"
        meta={<PaperModeIndicator active />}
      />,
    );
    expect(screen.getByRole("heading", { level: 1, name: "Dashboard" })).toBeInTheDocument();
    expect(screen.getByText("Paper workspace")).toBeInTheDocument();
    expect(screen.getByTestId("paper-mode-indicator")).toHaveAttribute(
      "aria-label",
      "Paper mode active",
    );
  });

  it("requires accessible label on IconButton", () => {
    render(
      <IconButton label="Refresh status" variant="ghost">
        <span>*</span>
      </IconButton>,
    );
    expect(screen.getByRole("button", { name: "Refresh status" })).toBeInTheDocument();
  });

  it("shows FieldError as alert text", () => {
    render(
      <>
        <Input aria-invalid="true" aria-describedby="err" />
        <FieldError id="err" message="Required" />
      </>,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Required");
  });

  it("renders RiskBlock without any override control", () => {
    render(<RiskBlock reason="Daily loss limit reached" ruleReference="daily_loss_limit" />);
    const block = screen.getByTestId("risk-block");
    expect(block).toHaveTextContent("Blocked");
    expect(block).toHaveTextContent("Daily loss limit reached");
    expect(block).toHaveTextContent("No override is available");
    expect(block.querySelector("button")).toBeNull();
  });

  it("pairs freshness state with icon and text", () => {
    render(<FreshnessPill state="stale" ageLabel="4m" />);
    expect(screen.getByTestId("freshness-pill")).toHaveTextContent("Stale · 4m");
  });

  it("uses tabular-numeral data numbers with signed channel", () => {
    const { container } = render(
      <DataNumber value="+12.5" tone="positive" signed numeric={12.5} />,
    );
    expect(container.querySelector(".font-data")).toHaveTextContent("▲ +12.5");
  });

  it("exposes tooltip content on focus", () => {
    render(
      <Tooltip content="Helpful hint">
        <Button type="button">Target</Button>
      </Tooltip>,
    );
    fireEvent.focus(screen.getByRole("button", { name: "Target" }));
    expect(screen.getByRole("tooltip")).toHaveTextContent("Helpful hint");
  });

  it("renders empty / error / stale / blocked / unavailable / limitations states", () => {
    render(
      <>
        <EmptyState title="Empty" description="Nothing here" />
        <ErrorState message="Failed" />
        <StaleState ageLabel="5m" />
        <LimitationsState message="Coverage incomplete" items={["Sample too small"]} />
        <BlockedState message="Not allowed" />
        <UnavailableState message="Down" />
      </>,
    );
    expect(screen.getByTestId("empty-state")).toHaveTextContent("Empty");
    expect(screen.getByTestId("error-state")).toHaveTextContent("Failed");
    expect(screen.getByTestId("stale-state")).toHaveTextContent("Stale data · 5m");
    expect(screen.getByTestId("limitations-state")).toHaveTextContent("Limitations");
    expect(screen.getByTestId("limitations-state")).toHaveTextContent("Coverage incomplete");
    expect(screen.getByTestId("limitations-state")).not.toHaveTextContent("Stale data");
    expect(screen.getByTestId("blocked-state")).toHaveTextContent("Blocked");
    expect(screen.getByTestId("unavailable-state")).toHaveTextContent("Down");
  });
});

describe("AT-040 PaperModeIndicator fail-closed", () => {
  it("defaults to unconfirmed paper mode", () => {
    render(<PaperModeIndicator />);
    expect(screen.getByTestId("paper-mode-indicator")).toHaveAttribute(
      "aria-label",
      "Paper mode not confirmed",
    );
  });

  it("shows confirmed paper mode only when active is true", () => {
    render(<PaperModeIndicator active />);
    expect(screen.getByTestId("paper-mode-indicator")).toHaveAttribute(
      "aria-label",
      "Paper mode active",
    );
  });

  it("treats real trading enabled as unconfirmed", () => {
    expect(isPaperModeConfirmed("paper", true)).toBe(false);
    render(<PaperModeIndicator active={isPaperModeConfirmed("paper", true)} />);
    expect(screen.getByTestId("paper-mode-indicator")).toHaveAttribute(
      "aria-label",
      "Paper mode not confirmed",
    );
  });

  it("treats non-paper execution mode as unconfirmed", () => {
    expect(isPaperModeConfirmed("live", false)).toBe(false);
    render(<PaperModeIndicator active={isPaperModeConfirmed("live", false)} />);
    expect(screen.getByTestId("paper-mode-indicator")).toHaveAttribute(
      "aria-label",
      "Paper mode not confirmed",
    );
  });

  it("treats missing safety data as unconfirmed", () => {
    expect(isPaperModeConfirmed(null, null)).toBe(false);
    expect(isPaperModeConfirmed(undefined, undefined)).toBe(false);
    render(<PaperModeIndicator active={isPaperModeConfirmed(null, null)} />);
    expect(screen.getByTestId("paper-mode-indicator")).toHaveAttribute(
      "aria-label",
      "Paper mode not confirmed",
    );
  });

  it("treats partial / loading safety data as unconfirmed", () => {
    expect(isPaperModeConfirmed("paper", null)).toBe(false);
    expect(isPaperModeConfirmed(null, false)).toBe(false);
    render(<PaperModeIndicator active={isPaperModeConfirmed("paper", null)} />);
    expect(screen.getByTestId("paper-mode-indicator")).toHaveAttribute(
      "aria-label",
      "Paper mode not confirmed",
    );
  });

  it("confirms only paper + real trading disabled", () => {
    expect(isPaperModeConfirmed("paper", false)).toBe(true);
  });
});

describe("AT-040 Tabs keyboard accessibility", () => {
  it("moves focus with ArrowRight and activates with Enter", () => {
    render(<ControlledTabs />);
    const tablist = screen.getByRole("tablist");
    screen.getByRole("tab", { name: "One" }).focus();
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Two" })).toHaveFocus();
    fireEvent.keyDown(tablist, { key: "Enter" });
    expect(screen.getByRole("tab", { name: "Two" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Panel B");
  });

  it("moves focus with ArrowLeft and activates with Space", () => {
    render(<ControlledTabs />);
    const tablist = screen.getByRole("tablist");
    screen.getByRole("tab", { name: "One" }).focus();
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Three" })).toHaveFocus();
    fireEvent.keyDown(tablist, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Two" })).toHaveFocus();
    fireEvent.keyDown(tablist, { key: " " });
    expect(screen.getByRole("tab", { name: "Two" })).toHaveAttribute("aria-selected", "true");
  });

  it("supports Home and End keys", () => {
    render(<ControlledTabs />);
    const tablist = screen.getByRole("tablist");
    screen.getByRole("tab", { name: "One" }).focus();
    fireEvent.keyDown(tablist, { key: "End" });
    expect(screen.getByRole("tab", { name: "Three" })).toHaveFocus();
    fireEvent.keyDown(tablist, { key: "Home" });
    expect(screen.getByRole("tab", { name: "One" })).toHaveFocus();
  });

  it("wraps focus at ends", () => {
    render(<ControlledTabs />);
    const tablist = screen.getByRole("tablist");
    screen.getByRole("tab", { name: "One" }).focus();
    fireEvent.keyDown(tablist, { key: "ArrowLeft" });
    expect(screen.getByRole("tab", { name: "Three" })).toHaveFocus();
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "One" })).toHaveFocus();
  });

  it("skips disabled tabs during keyboard navigation", () => {
    render(<ControlledTabs withDisabled />);
    const tablist = screen.getByRole("tablist");
    screen.getByRole("tab", { name: "One" }).focus();
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Three" })).toHaveFocus();
    expect(screen.getByRole("tab", { name: "Two" })).toBeDisabled();
  });

  it("keeps click activation working", () => {
    render(<ControlledTabs />);
    fireEvent.click(screen.getByRole("tab", { name: "Three" }));
    expect(screen.getByRole("tab", { name: "Three" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Panel C");
  });

  it("links tabs and panels with aria-controls / aria-labelledby when idPrefix is explicit", () => {
    render(<ControlledTabs idPrefix="link" />);
    const selected = screen.getByRole("tab", { name: "One" });
    expect(selected).toHaveAttribute("id", "link-tab-a");
    expect(selected).toHaveAttribute("aria-controls", "link-tabpanel-a");
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveAttribute("id", "link-tabpanel-a");
    expect(panel).toHaveAttribute("aria-labelledby", "link-tab-a");
  });
});

describe("AT-040 Tabs default identifier coherence", () => {
  it("resolves aria-controls and aria-labelledby without a manual idPrefix", () => {
    render(<ControlledTabs />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.length).toBe(3);

    for (const tab of tabs) {
      const controls = tab.getAttribute("aria-controls");
      expect(controls).toBeTruthy();
      const panel = document.getElementById(controls!);
      expect(panel).not.toBeNull();
      expect(panel).toHaveAttribute("role", "tabpanel");
      expect(panel!.getAttribute("aria-labelledby")).toBe(tab.id);
      expect(document.getElementById(tab.id)).toBe(tab);
    }

    const panels = screen.getAllByRole("tabpanel", { hidden: true });
    expect(panels.length).toBe(3);
    for (const panel of panels) {
      const labelledBy = panel.getAttribute("aria-labelledby");
      expect(labelledBy).toBeTruthy();
      const tab = document.getElementById(labelledBy!);
      expect(tab).not.toBeNull();
      expect(tab).toHaveAttribute("role", "tab");
      expect(tab!.getAttribute("aria-controls")).toBe(panel.id);
    }
  });

  it("keeps generated tab and panel identifiers matched within one group", () => {
    render(<ControlledTabs />);
    const prefix = screen.getByRole("tablist").getAttribute("data-tabs-prefix");
    expect(prefix).toBeTruthy();
    expect(screen.getByRole("tab", { name: "One" }).id).toBe(`${prefix}-tab-a`);
    expect(document.getElementById(`${prefix}-tabpanel-a`)).not.toBeNull();
    expect(document.getElementById(`${prefix}-tabpanel-b`)).not.toBeNull();
    expect(document.getElementById(`${prefix}-tabpanel-c`)).not.toBeNull();
  });
});

describe("AT-040 Tabs multiple groups", () => {
  it("generates distinct prefixes and avoids duplicate ids across independent groups", () => {
    function DualGroups(): ReactElement {
      const [first, setFirst] = useState("a");
      const [second, setSecond] = useState("a");
      return (
        <>
          <TabsRoot value={first} onChange={setFirst}>
            <Tabs
              aria-label="First"
              items={[
                { id: "a", label: "A1" },
                { id: "b", label: "B1" },
              ]}
            />
            <TabPanel id="a">First A</TabPanel>
            <TabPanel id="b">First B</TabPanel>
          </TabsRoot>
          <TabsRoot value={second} onChange={setSecond}>
            <Tabs
              aria-label="Second"
              items={[
                { id: "a", label: "A2" },
                { id: "b", label: "B2" },
              ]}
            />
            <TabPanel id="a">Second A</TabPanel>
            <TabPanel id="b">Second B</TabPanel>
          </TabsRoot>
        </>
      );
    }

    render(<DualGroups />);
    const first = screen.getByRole("tablist", { name: "First" });
    const second = screen.getByRole("tablist", { name: "Second" });
    const prefix1 = first.getAttribute("data-tabs-prefix");
    const prefix2 = second.getAttribute("data-tabs-prefix");
    expect(prefix1).toBeTruthy();
    expect(prefix2).toBeTruthy();
    expect(prefix1).not.toBe(prefix2);

    const allIds = Array.from(document.querySelectorAll("[id]")).map((el) => el.id);
    expect(new Set(allIds).size).toBe(allIds.length);

    const firstA = within(first).getByRole("tab", { name: "A1" });
    const secondA = within(second).getByRole("tab", { name: "A2" });
    expect(firstA.id).not.toBe(secondA.id);
    expect(document.getElementById(firstA.getAttribute("aria-controls")!)).toHaveTextContent(
      "First A",
    );
    expect(document.getElementById(secondA.getAttribute("aria-controls")!)).toHaveTextContent(
      "Second A",
    );

    fireEvent.click(within(second).getByRole("tab", { name: "B2" }));
    expect(screen.getByRole("tabpanel", { name: "B2" })).toHaveTextContent("Second B");
    expect(screen.getByRole("tabpanel", { name: "A1" })).toHaveTextContent("First A");
  });
});

describe("AT-040 Tabs panel lifecycle", () => {
  it("keeps inactive panels in the DOM with hidden and updates on switch", () => {
    render(<ControlledTabs />);
    const panels = screen.getAllByRole("tabpanel", { hidden: true });
    expect(panels).toHaveLength(3);

    const visible = screen.getByRole("tabpanel");
    expect(visible).toHaveTextContent("Panel A");
    expect(visible).not.toHaveAttribute("hidden");

    const hiddenPanels = panels.filter((panel) => panel.hasAttribute("hidden"));
    expect(hiddenPanels).toHaveLength(2);

    fireEvent.click(screen.getByRole("tab", { name: "Two" }));
    expect(screen.getByRole("tabpanel")).toHaveTextContent("Panel B");
    expect(screen.getByRole("tabpanel")).not.toHaveAttribute("hidden");

    const after = screen.getAllByRole("tabpanel", { hidden: true });
    expect(after).toHaveLength(3);
    expect(after.filter((panel) => panel.hasAttribute("hidden"))).toHaveLength(2);
  });

  it("reveals the correct panel via keyboard activation", () => {
    render(<ControlledTabs />);
    const tablist = screen.getByRole("tablist");
    screen.getByRole("tab", { name: "One" }).focus();
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    fireEvent.keyDown(tablist, { key: "Enter" });
    const panel = screen.getByRole("tabpanel");
    expect(panel).toHaveTextContent("Panel B");
    expect(panel).not.toHaveAttribute("hidden");
    expect(panel).toHaveAttribute("role", "tabpanel");
  });
});

describe("AT-040 Tabs roving focus robustness", () => {
  it("falls back to the first enabled tab when selected value is missing", () => {
    render(<ControlledTabs initialValue="missing" />);
    const tabs = screen.getAllByRole("tab");
    expect(tabStopCount(tabs)).toBe(1);
    expect(screen.getByRole("tab", { name: "One" }).tabIndex).toBe(0);
    expect(screen.getByRole("tab", { name: "Two" }).tabIndex).toBe(-1);
  });

  it("falls back to the first enabled tab when selected value is disabled", () => {
    render(
      <ControlledTabs
        withDisabled
        initialValue="b"
        items={[
          { id: "a", label: "One" },
          { id: "b", label: "Two", disabled: true },
          { id: "c", label: "Three" },
        ]}
      />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabStopCount(tabs)).toBe(1);
    expect(screen.getByRole("tab", { name: "One" }).tabIndex).toBe(0);
    expect(screen.getByRole("tab", { name: "Two" })).toBeDisabled();
    expect(screen.getByRole("tab", { name: "Two" }).tabIndex).toBe(-1);
  });

  it("keeps exactly one enabled tab stop and skips disabled tabs", () => {
    render(<ControlledTabs withDisabled />);
    const tabs = screen.getAllByRole("tab");
    expect(tabStopCount(tabs)).toBe(1);
    expect(screen.getByRole("tab", { name: "Two" }).tabIndex).toBe(-1);

    const tablist = screen.getByRole("tablist");
    screen.getByRole("tab", { name: "One" }).focus();
    fireEvent.keyDown(tablist, { key: "ArrowRight" });
    expect(screen.getByRole("tab", { name: "Three" })).toHaveFocus();
    expect(tabStopCount(screen.getAllByRole("tab"))).toBe(1);
  });

  it("does not leave a disabled tab as the only tab stop when all are disabled", () => {
    render(
      <ControlledTabs
        items={[
          { id: "a", label: "One", disabled: true },
          { id: "b", label: "Two", disabled: true },
        ]}
        initialValue="a"
      />,
    );
    const tabs = screen.getAllByRole("tab");
    expect(tabs.every((tab) => (tab as HTMLButtonElement).disabled)).toBe(true);
    expect(tabs.every((tab) => tab.tabIndex === -1)).toBe(true);
    expect(tabStopCount(tabs)).toBe(0);
  });

  it("moves the tab stop when the focused tab is removed from items", () => {
    function RemovableTabs(): ReactElement {
      const [items, setItems] = useState([
        { id: "a", label: "One" },
        { id: "b", label: "Two" },
        { id: "c", label: "Three" },
      ]);
      const [value, setValue] = useState("c");
      return (
        <>
          <button type="button" onClick={() => setItems((prev) => prev.filter((i) => i.id !== "c"))}>
            Remove C
          </button>
          <TabsRoot value={value} onChange={setValue}>
            <Tabs aria-label="Removable" items={items} />
            {items.map((item) => (
              <TabPanel key={item.id} id={item.id}>
                Panel {item.id}
              </TabPanel>
            ))}
          </TabsRoot>
        </>
      );
    }

    render(<RemovableTabs />);
    expect(screen.getByRole("tab", { name: "Three" }).tabIndex).toBe(0);
    fireEvent.click(screen.getByRole("button", { name: "Remove C" }));
    const remaining = screen.getAllByRole("tab");
    expect(remaining).toHaveLength(2);
    expect(tabStopCount(remaining)).toBe(1);
    expect(screen.getByRole("tab", { name: "One" }).tabIndex).toBe(0);
  });
});

describe("AT-040 phase 2 primitives", () => {
  it("keeps the mode pill unverified until paper is confirmed", () => {
    render(<ModePill />);
    expect(screen.getByTestId("mode-pill")).toHaveAttribute("data-kind", "unverified");
    expect(screen.getByTestId("mode-pill")).not.toHaveTextContent("PAPER");
  });

  it("moves a segmented control with the arrow keys", () => {
    function Harness(): ReactElement {
      const [value, setValue] = useState("demo");
      return (
        <SegmentedControl
          aria-label="Account scope"
          value={value}
          onChange={setValue}
          options={[
            { value: "demo", label: "Demo", tone: "demo" },
            { value: "simulator", label: "Simulator", tone: "paper" },
          ]}
        />
      );
    }
    render(<Harness />);
    const group = screen.getByRole("radiogroup", { name: "Account scope" });
    screen.getByRole("radio", { name: "Demo" }).focus();
    fireEvent.keyDown(group, { key: "ArrowRight" });
    expect(screen.getByRole("radio", { name: "Simulator" })).toHaveAttribute("aria-checked", "true");
  });

  it("toggles a filter chip from the keyboard", () => {
    function Harness(): ReactElement {
      const [value, setValue] = useState<string[]>([]);
      return (
        <FilterChips
          aria-label="Journal filters"
          value={value}
          onChange={setValue}
          options={[
            { id: "notes", label: "Needs notes", count: 3 },
            { id: "winners", label: "Winners" },
          ]}
        />
      );
    }
    render(<Harness />);
    const toolbar = screen.getByRole("toolbar", { name: "Journal filters" });
    screen.getByRole("button", { name: "Needs notes 3" }).focus();
    fireEvent.keyDown(toolbar, { key: "Enter" });
    expect(screen.getByRole("button", { name: "Needs notes 3" })).toHaveAttribute("aria-pressed", "true");
  });
});
