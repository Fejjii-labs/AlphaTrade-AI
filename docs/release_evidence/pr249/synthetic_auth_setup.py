"""Create/login an isolated staging smoke identity after supported egress is applied.

Uses only health/auth routes. No orders, provider calls, budget/operator changes,
verification bypass, token minting or credential logging. Retains one exact identity
across uncertain responses and always tries login before registration.
"""
import json
import os
from pathlib import Path
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

HOST = "alphatrade-api-staging.onrender.com"
BASE = "https://" + HOST
PRIVATE = Path("/workspace/AlphaTrade-AI/.ai/local/pr249-release/synthetic-auth.json")

def safe_write(value):
    PRIVATE.parent.mkdir(parents=True, exist_ok=True)
    temporary = PRIVATE.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as output:
        json.dump(value, output)
    os.chmod(temporary, 0o600)
    os.replace(temporary, PRIVATE)

def request(path, body=None, token=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        # Do not expose request or error bodies, tokens, headers or credentials.
        return error.code, None

def main():
    policy = json.loads(Path("/etc/codex/network-policy.json").read_text())
    hosts = {row["host"] for row in policy["http_network_policy"]["egress_rules"]}
    if HOST not in hosts:
        print(json.dumps({"ready": False, "reason": "staging_host_not_in_runtime_policy", "network_requests": 0}))
        return 2
    supplied = bool(os.getenv("SMOKE_EMAIL") and os.getenv("SMOKE_PASSWORD"))
    if supplied:
        auth = {"email": os.environ["SMOKE_EMAIL"], "password": os.environ["SMOKE_PASSWORD"], "allow_registration": False}
    elif PRIVATE.exists():
        if PRIVATE.stat().st_mode & 0o077:
            raise ValueError("private_auth_permissions_invalid")
        auth = json.loads(PRIVATE.read_text())
    else:
        auth = {"email": "release-smoke-60a5550-" + secrets.token_hex(8) + "@example.com",
                "password": secrets.token_urlsafe(32), "organization_name": "AlphaTrade synthetic release smoke 60a5550",
                "allow_registration": True}
        safe_write(auth)
    status, health = request("/health")
    if status != 200 or not isinstance(health, dict) or health.get("environment") != "staging" or health.get("execution_mode") != "paper" or health.get("real_trading_enabled") is not False:
        print(json.dumps({"ready": False, "reason": "paper_safety_health_unverified", "http_status": status}))
        return 2
    credentials = {"email": auth["email"], "password": auth["password"]}
    status, login = request("/auth/login", credentials)
    if status == 401 and auth.get("allow_registration"):
        if health.get("must_verify_email") is not False:
            print(json.dumps({"ready": False, "reason": "current_normal_registration_login_requirement_unverified"}))
            return 2
        status, registration = request("/auth/register", credentials | {"organization_name": auth["organization_name"]})
        if status not in [201, 422]:
            print(json.dumps({"ready": False, "reason": "registration_unconfirmed_login_before_any_retry", "http_status": status}))
            return 2
        status, login = request("/auth/login", credentials)
    if status != 200 or not isinstance(login, dict):
        print(json.dumps({"ready": False, "reason": "actual_login_not_accepted", "http_status": status}))
        return 2
    tokens = login.get("tokens", {})
    token = tokens.get("access_token")
    if not token:
        raise ValueError("login_token_missing")
    status, identity = request("/auth/me", token=token)
    if (status != 200 or not isinstance(identity, dict)
            or identity.get("user", {}).get("email", "").casefold() != auth["email"].casefold()
            or not identity.get("organization", {}).get("id")
            or identity.get("organization", {}).get("id") != login.get("organization", {}).get("id")):
        print(json.dumps({"ready": False, "reason": "authenticated_identity_unverified", "http_status": status}))
        return 2
    auth.update({"access_token": token, "login_verified": True, "identity_verified": True, "allow_registration": False})
    safe_write(auth)
    print(json.dumps({"ready": True, "actual_login_verified": True, "authenticated_identity_verified": True,
                      "private_auth_file": str(PRIVATE), "paid_provider_calls": 0, "exchange_orders": 0}))
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({"ready": False, "reason": "auth_setup_incomplete", "error_type": type(error).__name__}))
        sys.exit(2)
