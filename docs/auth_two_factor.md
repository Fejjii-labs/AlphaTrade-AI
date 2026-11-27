# Two-factor sign-in (TOTP)

Optional second step at sign-in: a 6-digit code from an authenticator app
(Google Authenticator, 1Password, Authy, Microsoft Authenticator, …), standard
RFC 6238 TOTP (SHA-1, 6 digits, 30-second steps).

## For users

**Turn it on.** Settings → Account → *Two-factor sign-in* → *Turn on two-factor sign-in*.
Scan the QR code with your app (or type the manual key), then enter the code the
app shows. You then see **10 recovery codes once**. Save them somewhere safe.
Each one signs you in once if you lose your phone.

**Signing in.** After your password, enter the current code from the app, or
choose *Use a recovery code*. Tick *Remember this device for 30 days* to skip the
code on this browser; the password is still always required.

**Lost codes or a new phone.** *New recovery codes* replaces all previous recovery
codes (needs a current app code). To move to a new phone, turn 2FA off (needs a
current app code) and turn it on again on the new phone.

**Remembered devices.** *Forget remembered devices* ends every "remember this
device" grant; each browser asks for a code at its next sign-in. Turning 2FA off
also forgets them.

**Too many wrong codes.** After 5 wrong codes within 15 minutes, code checks for
your account are locked for 15 minutes (sign-in second step, turning off and new
recovery codes share this budget). Wait, then try again.

## For operators

### API

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `POST /auth/login` | none | Password step. With 2FA on, returns `{two_factor_required: true, pending_token, expires_in}` instead of the session, unless the body's `device_token` is a live remembered-device grant for that user. |
| `POST /auth/login/two-factor` | none | `{pending_token, code \| recovery_code, remember_device}` → session tokens (and `device_token` when asked). |
| `GET /auth/two-factor` | bearer | Status: on/off, recovery codes left, remembered devices, lock. |
| `POST /auth/two-factor/setup` | bearer | New secret: `manual_key`, `otpauth_uri`, `qr_svg_data_uri`. Refused (409) while 2FA is on. |
| `POST /auth/two-factor/confirm` | bearer | `{code}` → 2FA on; returns 10 recovery codes (shown once). |
| `POST /auth/two-factor/recovery-codes` | bearer | `{code}` (current TOTP) → 10 new codes; old ones stop working. |
| `POST /auth/two-factor/disable` | bearer | `{code}` (current TOTP; recovery codes are not accepted). Deletes the secret and codes, revokes remembered devices. |
| `POST /auth/two-factor/devices/revoke` | bearer | Revoke every remembered device for the user. |

### Behaviour

- Codes are accepted for the current 30 s step ±1 step. An accepted step is
  stored (`user_two_factor.last_used_step`); the same or an older step is refused,
  so a code cannot be replayed.
- The pending token is a JWT with `type=two_factor_pending`, valid for
  `TWO_FACTOR_PENDING_TOKEN_SECONDS` (default 300). It is not an access token
  (`/auth/me` rejects it) and it is single use: its `jti` goes on the access-token
  denylist when the second step succeeds.
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
  derived from `JWT_SECRET` with HKDF-SHA256 under a two-factor label. **Set a
  dedicated key before users enrol in staging/production**: with the derived key,
  rotating `JWT_SECRET` makes every stored secret and recovery code unreadable,
  and users must re-enrol (an operator can delete their `user_two_factor`,
  `two_factor_recovery_codes` and `two_factor_trusted_devices` rows). Rotating
  `JWT_SECRET` also ends all remembered devices and pending sign-ins (both are
  JWTs). Generate a key with
  `python -c "import base64,secrets;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"`.
  A malformed key stops the app at start-up.

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
