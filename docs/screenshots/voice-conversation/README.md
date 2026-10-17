# Compact voice fixture evidence

Standalone Vite component harness with deterministic Web Speech and Agent transport
fixtures, captured by `frontend/ui-tests/voice-conversation.spec.ts` in Chromium.
No microphone, backend, paid speech service, orders or user account data.

| Viewport | Listening | Speaking |
| --- | --- | --- |
| 320×720 | [narrow](narrow-listening.png) | [narrow](narrow-speaking.png) |
| 390×844 | [phone](phone-listening.png) | [phone](phone-speaking.png) |
| 1440×1000 | [desktop](desktop-listening.png) | [desktop](desktop-speaking.png) |

Controls wrap inside the composer; browser checks assert no horizontal overflow
and at least 44px button heights. Screenshots show only the voice component region.
Native recognition, hardware audio, Safari and physical mobile browsers are unverified.
