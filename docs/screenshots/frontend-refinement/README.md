# PR249 frontend refinement screenshots

All images show **FRONTEND TEST FIXTURE · NOT LIVE DATA**. No account, backend or
provider is used. Agent captures deliberately show the explicit unavailable voice
state; this does not represent the release owner's live acceptance result.

| Surface | Desktop 1440×1000 | Phone 390×844 | Landscape 844×390 |
| --- | --- | --- | --- |
| Agent: conversation, upload, interactive voice | [Desktop](agent-desktop.png) | [Phone](agent-phone.png) | [Landscape](agent-landscape.png) |
| Knowledge: categories and full-width search | [Desktop](knowledge-desktop.png) | [Phone](knowledge-phone.png) | [Landscape](knowledge-landscape.png) |
| Strategies: expandable recent detections | [Desktop](strategies-desktop.png) | [Phone](strategies-phone.png) | [Landscape](strategies-landscape.png) |
| Settings: preserved monitoring controls | [Desktop](settings-desktop.png) | [Phone](settings-phone.png) | [Landscape](settings-landscape.png) |

Settings captures include an unsaved local checkbox edit; no monitoring write is
sent. Full-page images retain page scrolling and show the header/navigation.
Chromium viewport emulation is not physical Safari, keyboard or microphone proof.
The existing offline font fixture is test-only and does not change runtime fonts.

From `frontend/`:

```sh
REFINEMENT_SCREENSHOTS=1 PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium npm exec -- playwright test --config playwright.refinement.config.ts --retries=0
```

See [the concise UI handoff](../../frontend_refinement_pr249_handoff.md) for scope,
exact baseline, acceptance and remaining gaps.
