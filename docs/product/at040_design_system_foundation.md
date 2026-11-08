# AT-040 — Premium Design-System Foundation (Phase A)

Status: implementation complete for Phase A scope (pending review merge).  
Depends on: AT-039 blueprint (`docs/product/at039_premium_ui_ux_blueprint.md`).  
Does not change: navigation architecture, routes, trading/execution/risk authority, notification delivery, chart stack.

## Purpose

Establish AlphaTrade’s reusable, dark-first design-system foundation so later phases can migrate screens without inventing one-off styles.

## Decisions carried from AT-039 (approved defaults)

| Open question | Phase A decision |
|---|---|
| Information architecture | Adopt the recommended **eight-destination** IA in later Phase B; Phase A does **not** rewrite nav |
| Obsolete routes | Consolidate later via **redirects**, not capability deletion |
| Risk surface | Split later between Portfolio status and Settings config; Phase A only adds `RiskBlock` primitive with **no UI override** |
| Theme | **Dark-first** tokens + `[data-theme="light"]` swap-ready structure |
| Push notifications | **Out of Phase A** |
| Charts | Keep blueprint recommendation (Lightweight Charts + Recharts in Phase D); **no new chart dependency** added here |

## Tokens

Source: `frontend/src/styles/tokens.css` (imported by `frontend/src/app/globals.css`).  
Tailwind mapping: `frontend/tailwind.config.ts`.

### Semantic color

- Surfaces: `--color-background`, `--color-surface-0/1/2`, `--color-surface-raised`
- Borders: `--color-border`, `--color-border-subtle`, `--color-border-strong`
- Text: `--color-text-primary|secondary|muted|inverse|disabled`
- Accent (paper-safe emerald): `--color-accent*`
- Status: `success`, `warning`, `danger`, `info`, `blocked`
- Trading directional: `positive` / `negative` (always paired with sign/icon)
- Indicators: `paper`, `stale`
- Focus: `--color-focus-ring`

### Spacing / radius / elevation

- 4px base scale (`--space-1`…`--space-12`)
- Radii: control 8px, card 12px, pill
- Elevation: prefer surface step + 1px border; light `shadow-sm/md` only when needed

### Theme structure

- Default / dark: `:root` and `[data-theme="dark"]`
- Light-ready: `[data-theme="light"]` token swap (enable in Phase E). Status 2026-11: the unused light block was removed from `tokens.css` (audit L20); reintroduce it with the theme switch.
- `prefers-reduced-motion` disables non-essential animation globally

## Typography

Utility classes in `globals.css`:

| Class | Role | Size |
|---|---|---|
| `.text-display` | Rare display (not for in-app page titles) | 1.75rem |
| `.text-heading` | Page title via `PageHeader` | 1.5rem |
| `.text-section` | Section titles | 1.125rem |
| `.text-body` | Supporting copy | 0.875rem |
| `.text-label` | Form labels | 0.875rem |
| `.text-caption` | Metadata | 0.75rem |
| `.font-data` | Trading metrics (tabular numerals + mono) | inherits |

Fonts: Inter (UI) + JetBrains Mono (data numerals) via `next/font` in `frontend/src/app/layout.tsx`.

## Primitives (`frontend/src/components/ui/`)

| Primitive | Maps to AT-039 Phase A |
|---|---|
| `Button`, `IconButton` | Tokenized actions + accessible icon buttons |
| `Input`, `Select`, `Textarea`, `Label`, `FieldError`, `FieldHint` | Forms + validation messages |
| `Card`, `Panel` | Surfaces / panels |
| `Badge` (+ paper/stale/blocked variants) | Badges / status pills |
| `Tabs`, `TabPanel` | Tabs (native a11y; no new dep) |
| `Tooltip` | Tooltips (native a11y; no new dep) |
| `Divider` | Dividers |
| `Skeleton`, `SkeletonCard`, `SkeletonText` | Loading skeletons |
| `PageHeader` | One title per page |
| `ContentContainer`, `PageSection` | Layout foundation (width + section spacing) |
| `FreshnessPill` | Stale / delayed / fallback indicators |
| `PaperModeIndicator` | Compact paper chip — **fail-closed** (default unconfirmed; `active` only when verified) |
| `VerifiedPaperModeIndicator` | Wires `PaperModeIndicator` to `/health` via `useSafetyPosture` |
| `RiskBlock` | Risk BLOCK panel — **no override control** |
| `DataNumber` | Tabular trading metrics |

Shared states (`frontend/src/components/states.tsx`): `EmptyState`, `LoadingState`, `ErrorState`, `StaleState` (freshness only), `LimitationsState` (analytical/coverage limitations — not stale), `BlockedState`, `UnavailableState`, `SuccessState`.

## Layout foundation

- App shell (`AppShell`) uses semantic `bg-background`, `max-w-content`, `space-y-section`, gutter tokens
- Top bar adopts `PaperModeIndicator` + tokenized borders/surfaces
- Breakpoints remain `sm/md/lg` (640 / 768 / 1024)
- **Navigation architecture unchanged** in Phase A (sidebar / bottom nav kept)

## Initial adoption (representative only)

| Surface | Change |
|---|---|
| Dashboard shell/header | `PageHeader`, paper indicator, tokenized shell/top bar |
| TradingView signal inbox | `PageHeader`, tokenized list selection, unavailable state |
| Paper signal orchestration | `PageHeader`, `BlockedState`, `RiskBlock` for blocked decisions |
| Journal statistics | `PageHeader`, `Select` primitive, `DataNumber` metrics |
| Portfolio summary | `PageHeader`, `LimitationsState` for analytical limitations (not stale), verified paper chip |

No routes removed. No full 53-route redesign.

## Accessibility (Phase A targets)

- Visible `:focus-visible` ring using `--color-focus-ring`
- Icon buttons require `aria-label`
- Status not color-only (icons/labels/signs)
- Risk BLOCK uses lock icon + “Blocked” text + rule reference
- `prefers-reduced-motion` support
- WCAG AA contrast targeted for token pairs on dark theme
- `Tabs`: ArrowLeft/Right, Home/End, wrap, skip disabled, Enter/Space activate; collision-safe ids + `aria-controls` / `aria-labelledby`

## Hardening notes (post Phase A review)

- Paper mode indicators must never default to active; unknown/loading/inconsistent posture → “Paper mode not confirmed”
- Analytical portfolio limitations use `LimitationsState`, not `StaleState`
- `StaleState` reserved for actual freshness degradation

## Remaining work (Phases B–F)

| Phase | Scope (from AT-039) |
|---|---|
| **B** | 8-destination nav shell, mobile bottom bar + Menu sheet, status strip merge, redirects for obsolete paths |
| **C** | Critical daily workflows (Signals inbox, Plan hub, Validate pipeline, Journal quick-entry, Portfolio merge, Dashboard attention queue) |
| **D** | Chart stack (Lightweight Charts + Recharts) + canonical analytics metric catalog |
| **E** | Mobile/PWA polish, notification opt-ins, auth polish, **enable light theme token swap** |
| **F** | Usability/consistency review, AA/keyboard audit, remove residual ad-hoc styling |

## Out of scope / safety

- No trading logic, execution, or risk-authority changes
- Live trading remains disabled
- No deploy from this slice
- No notification delivery
- No large new UI/chart dependency

## Validation checklist

- Frontend lint / typecheck / targeted vitest / production build
- Representative page tests still pass
- Responsive review at desktop (~1280) and phone (~390) widths
- Backend/API behavior unchanged (frontend-only)
