# Two-factor sign-in (TOTP)

Optional second step at sign-in: a 6-digit code from an authenticator app
(Google Authenticator, 1Password, Authy, Microsoft Authenticator, …), standard
RFC 6238 TOTP (SHA-1, 6 digits, 30-second steps).

## For users

**Turn it on.** Settings → Account → *Two-factor sign-in* → *Turn on two-factor sign-in*.
Enter your current password, scan the QR code with your app (or type the manual
key), then enter the code the app shows. You then see **10 recovery codes once**.
Save them somewhere safe. Each one signs you in once if you lose your phone.
Turning it on signs out your other sessions (other browsers and devices); the
browser you used stays signed in.

**Signing in.** After your password, enter the current code from the app, or
choose *Use a recovery code*. Tick *Remember this device for 30 days* to skip the
code on this browser; the password is still always required.

**Lost codes or a new phone.** *New recovery codes* replaces all previous recovery
codes (needs a current app code). To move to a new phone, turn 2FA off and turn
it on again on the new phone. Turning it off needs your password plus a current
app code, or (if the phone is lost) one recovery code.

**Remembered devices.** *Forget remembered devices* ends every "remember this
device" grant; each browser asks for a code at its next sign-in. Turning 2FA off
or resetting your password also forgets them.

**Too many wrong codes.** After 5 wrong codes within 15 minutes, code checks for
your account are locked for 15 minutes (sign-in second step, turning off and new
recovery codes share this budget). Wait, then try again.

## For operators

### API

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `POST /auth/login` | none | Password step. With 2FA on, returns `{two_factor_required: true, pending_token, expires_in}` instead of the session, unless the body's `device_token` is a live remembered-device grant for that user. |
| `POST /auth/login/two-factor` | none | `{pending_token, code \| recovery_code, remember_device}` → session tokens (and `device_token` when asked). |
| `GET /auth/two-factor` | bearer | Status: on/off, `enrolment_available`, recovery codes left, remembered devices, lock. |
| `POST /auth/two-factor/setup` | bearer | `{password}` → new secret: `manual_key`, `otpauth_uri`, `qr_svg_data_uri`. Refused (409) while 2FA is on; 503 `two_factor_unavailable` when enrolment is not configured (below). |
| `POST /auth/two-factor/confirm` | bearer | `{code}` → 2FA on; returns 10 recovery codes (shown once), `revoked_sessions`, and a fresh `tokens` pair for this session. Every other refresh token of the user is revoked. |
| `POST /auth/two-factor/recovery-codes` | bearer | `{code}` (current TOTP) → 10 new codes; old ones stop working. |
| `POST /auth/two-factor/disable` | bearer | `{password, code \| recovery_code}`. Deletes the secret and codes, revokes remembered devices. |
| `POST /auth/two-factor/devices/revoke` | bearer | Revoke every remembered device for the user. |

### Behaviour

- Codes are accepted for the current 30 s step ±1 step. An accepted step is
  stored (`user_two_factor.last_used_step`); the same or an older step is refused,
  so a code cannot be replayed.
- The pending token is a JWT with `type=two_factor_pending`, valid for
  `TWO_FACTOR_PENDING_TOKEN_SECONDS` (default 300). It is not an access token
  (`/auth/me` rejects it) and it is single use: on success its `jti` is written to
  `two_factor_used_pending_tokens` in the same transaction that holds the
  `user_two_factor` row lock (`SELECT … FOR UPDATE`), so two concurrent
  redemptions cannot both succeed. This does not depend on the access-token
  denylist. A replayed token is refused before any code check (nothing counted,
  no recovery code spent). Expired rows are pruned per user.
- A wrong password on setup or disable answers 400 `current_password_invalid`
  (audited as `auth_two_factor_failed` with `method=password`) and is checked
  before any code, so it never spends a TOTP step or recovery code.
- Turning 2FA on revokes every refresh token of the user and returns a fresh pair.
  Access tokens already issued elsewhere stay valid until they expire
  (`ACCESS_TOKEN_EXPIRE_MINUTES`); they cannot be refreshed.
- A password reset revokes all refresh tokens (existing behaviour) and every
  remembered-device grant.
- Lock: `TWO_FACTOR_MAX_FAILURES` (5) failures within
  `TWO_FACTOR_FAILURE_WINDOW_SECONDS` (900) lock code checks for that user for
  `TWO_FACTOR_LOCKOUT_SECONDS` (900); the API answers 429 `two_factor_locked`.
  Counters live on the `user_two_factor` row, so they hold across API instances.
  The second step also has an IP limit (30 per 15 minutes).
- Remember device: a JWT with `type=two_factor_device` naming a
  `two_factor_trusted_devices` row, valid `TWO_FACTOR_REMEMBER_DEVICE_DAYS` (30).
  It is honoured only while the row is unrevoked and unexpired and belongs to the
  same user. The web app keeps it in `localStorage` per email.
- Wrong codes on authenticated actions return 400 `two_factor_code_invalid` (not
  401, so the browser session is not dropped).

### Storage

- `user_two_factor.secret_ciphertext`: AES-256-GCM (`cryptography`), random
  96-bit nonce, user id as associated data (a row copied to another user does not
  decrypt). Format `v1:<base64url(nonce || ciphertext)>`.
- `two_factor_recovery_codes.code_hash`: HMAC-SHA256 of user id + normalised code;
  used codes keep `used_at`.
- Key: `TWO_FACTOR_ENCRYPTION_KEY` (base64url, 32 bytes). If empty, a key is
  derived from `JWT_SECRET` with HKDF-SHA256 under a two-factor label. **Outside
  `ENVIRONMENT=local` the server refuses new enrolment until a dedicated key is
  set** (setup and confirm answer 503 `two_factor_unavailable`; status reports
  `enrolment_available: false`). The app still starts without it. With the derived key,
  rotating `JWT_SECRET` makes every stored secret and recovery code unreadable,
  and users must re-enrol (an operator can delete their `user_two_factor`,
  `two_factor_recovery_codes` and `two_factor_trusted_devices` rows). Rotating
  `JWT_SECRET` also ends all remembered devices and pending sign-ins (both are
  JWTs). Generate a key with
  `python -c "import base64,secrets;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`.
  A malformed key stops the app at start-up.
- If a stored secret no longer decrypts (key changed or lost), code checks for that
  user answer 503 `two_factor_unavailable`, log `two_factor_secret_unreadable`
  and audit `auth_two_factor_failed` with `reason=secret_unreadable`. This is not
  counted as a wrong code and never locks the user. Recovery codes use the same
  key, so they are refused the same way; the user needs the operator recovery
  below.
- `scripts/fresh_start_purge.py` keeps all three tables (like `users`), so a
  fresh-start purge does not turn anyone's 2FA off.

### Audit

Every outcome is written to `audit_logs` with `resource_type=auth_two_factor`
and `resource_id=<user id>`: `auth_two_factor_setup_started`, `_enabled`,
`_disabled`, `_verified` (metadata `action`, `method`: `totp`, `recovery_code`
or `remembered_device`), `_failed`, `_locked`, `_codes_regenerated`,
`_devices_revoked`. Failures and locks are committed in their own transaction so
they survive the failed request. Codes, secrets and digests are never logged or
audited.

### Recovery for a locked-out user

If a user lost both the phone and the recovery codes, an operator (after
verifying identity out of band) deletes that user's rows in `user_two_factor`,
`two_factor_recovery_codes` and `two_factor_trusted_devices`. The next sign-in
needs only the password; the user should turn 2FA on again.

### Step-up (not built yet)

`TwoFactorService.verify_code_for_action(user_id=..., organization_id=..., code=..., action=...)`
is the hook for requiring a fresh code before a sensitive action (for example
arming real trading). It applies the same lock, replay guard and audit. A
step-up feature adds a `TwoFactorAction` member and calls it from that route.
Nothing calls it for step-up today.

### Known follow-ups (not in this change)

- **Lockout policy.** The lock is per user, so someone who knows the password can
  keep a user's code step locked (denial of service). Wrong passwords on setup and
  disable are rate-limited per tenant (30 per 15 minutes) and audited but do not
  count toward the 2FA lock.
- **Remembered-device token in `localStorage`.** Script running on the web origin
  (XSS) could read it. It only skips the code step: the password is still
  required, and the grant is revocable. An httpOnly cookie would be stronger.
- **Postgres-only lock tests.** The row lock and concurrent pending-token
  redemption rely on `SELECT … FOR UPDATE` and the primary key. The unit tests run
  on SQLite and check the sequential behaviour; there is no concurrent test
  against Postgres yet.
- **Key rotation.** Ciphertexts carry a format version (`v1:`) but no key id;
  rotating `TWO_FACTOR_ENCRYPTION_KEY` today means users re-enrol.
