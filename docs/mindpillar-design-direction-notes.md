## Design direction: MindPillar-inspired (phases 3-9)

Remaining redesign phases (3-9, on top of PRs 276/277) take their look and feel
from MindPillar (https://mindpillar.com, app CSS `/assets/index-B6CAGr97.css`,
captured 2026-10-19). **Inspired by, not a copy:** no MindPillar logo, copy,
illustrations or chart widgets. Keep AlphaTrade's semantic colour rules
(violet = PAPER, cyan = DEMO, green/red = PnL) and the existing token *names* in
`frontend/src/styles/tokens.css`; retune values toward the targets below.

### Observed MindPillar tokens (dark theme is the default)
| Role | Dark | Light | Source |
|---|---|---|---|
| Background | `#07090f` | `#eceef4` | `--bg`, `.dark{--background}` |
| Surface / muted | `#0c1020` | `#fff` | `--surface`, `--muted` |
| Card | `#0f1420` | `#fff` | `--card` |
| Secondary / accent fill | `#141a28` | `#e8eaf2` | `--secondary`, `--accent` |
| Border | `#1c2438` | `#c8cdde` | `--border` |
| Subtle border | `#111827` | `#dde0eb` | `--bsub` |
| Text 1 / 2 / 3 | `#eef2f8` / `#7d8ba0` / `#3a4558` | `#0a0e1a` / `#374151` / `#6b7280` | `--t1..t3` |
| Primary (brand violet-blue) | `#5a6aff` | — | `--primary`, Webflow `primary-500` |
| Interactive blue | `#2563eb` (soft `#2563eb1f`) | `#1d4ed8` | `--blue`, `--bsoft` |
| Highlight cyan | `#47d7ff` | — | `--blue-accent` |
| Orange (eyebrow) | `#f97316` | — | `--secondary-500` |
| Up / down | `#10b981` / `#ef4444` (also `#f36`) | `#047857` / `#b91c1c` | `--green`, `--red`, `--mp-down` |
| Amber | `#f59e0b` | `#b45309` | `--mp-amber` |
| Dark violet (deep brand) | `#020042` | — | `--mp-dark-violet` |

- **Font:** Figtree (variable), used for both UI and numbers (`--font-sans`, `--font-mono` both Figtree). Marketing site also uses Inter / Inter Tight headlines.
- **Radius:** base `--radius: .625rem` (10px); most-used values 6px, 8px, 4px, 10px, 12px; pills 50%.
- **Density:** compact/data-dense. Most frequent font sizes 11/13/12/10/14px; gaps 8/6/10/12/4/16px; 4px spacing unit (`--spacing: .25rem`).
- **Layout:** content max-widths 1200–1280px app, 720–880px reading columns. Subtle glass on overlays (`backdrop-filter: blur(3-10px)`).
- **Aesthetic:** near-black navy, flat cards separated by thin borders rather than shadows, one violet-blue brand accent, small type, calm "terminal" feel.
- **Not observed:** nav structure from CSS alone (rendered nav not inspected), shadow scale, motion specs.

### Rules for phases 3-9
1. Dark is default; light theme uses the light column.
2. Retune `tokens.css` values toward the table (background `#07090f`, surfaces `#0c1020`/`#0f1420`, border `#1c2438`, text `#eef2f8`/`#7d8ba0`); keep variable names; keep AA contrast (`tokens.contrast.test.ts` must pass — `--t3 #3a4558` is decorative only, never body text).
3. Brand accent `#5a6aff` for primary actions/focus; blue `#2563eb` stays for links/interactive; orange only as eyebrow.
4. Switch the UI font to Figtree (self-hosted `@fontsource-variable/figtree`), tabular numerals for prices/PnL.
5. Dense by default: 13px body in tables/panels, 11–12px labels, 8px gaps, 10px card radius, 6px controls.
6. Flat cards with 1px borders; no heavy shadows; blur only on dialogs/sticky headers.
7. Safety badges (PAPER/DEMO/kill-switch/pause) keep their existing meaning and stay the most prominent elements.
