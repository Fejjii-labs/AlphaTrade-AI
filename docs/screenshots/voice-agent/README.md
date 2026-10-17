# Actual Agent voice fixture evidence

Captured from `/agent` with `frontend/ui-tests/voice-agent.spec.ts` in Chromium.
The real browser provider receives simulated Web Speech events and the existing
Agent API receives synthetic contract fixtures. These are not microphone, Safari,
iPhone or live provider checks.

| Viewport | Integrated speaking controls |
| --- | --- |
| 320×720 | [Narrow](narrow-integrated-speaking.png) |
| 390×844 | [Phone size](phone-integrated-speaking.png) |
| 1440×1000 | [Desktop](desktop-integrated-speaking.png) |

Each case creates a conversation using the existing flow, completes two spoken
turns, checks 44px controls and horizontal fit, then navigates away and back without
leaving acknowledged voice blocked. Other cases cover editable empty review,
typed-draft preservation, exact attachment recovery after reload and typed fallback.
