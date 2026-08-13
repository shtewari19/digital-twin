"""Obtain a user JWT for local backend testing (no frontend needed).

Uses Entra ID device-code flow: you open a URL, sign in once, and this
script prints an access token you can paste into Swagger or curl.

Usage (from apps/api/, with .env configured):
    pip install msal
    python scripts/get_dev_token.py

Then:
    curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/me

Or open http://localhost:8000/docs → Authorize → paste the token.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

try:
    import msal
except ImportError:
    print("❌ msal is required:  pip install msal")
    sys.exit(1)


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _decode_claims(token: str) -> dict:
    """Decode JWT payload without verifying (display only)."""
    try:
        payload = token.split(".")[1]
        padding = "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload + padding))
    except Exception:
        return {}


def main() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    _load_env_file(env_path)

    client_id = os.environ.get("APP_ENTRA_CLIENT_ID")
    tenant_id = os.environ.get("APP_ENTRA_TENANT_ID")
    if not client_id or not tenant_id:
        print(f"❌ Set APP_ENTRA_CLIENT_ID and APP_ENTRA_TENANT_ID in {env_path}")
        sys.exit(1)

    # MSAL forbids reserved scopes (openid/profile/offline_access) as input.
    # User.Read works without Expose an API; script prefers id_token for /me.
    # After Expose an API: APP_ENTRA_SCOPE=api://<client-id>/access_as_user
    default_scope = "User.Read"
    scope = os.environ.get("APP_ENTRA_SCOPE", default_scope)
    scopes = [s for s in scope.split() if s]

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.PublicClientApplication(client_id, authority=authority)

    print(f"Authority : {authority}")
    print(f"Client ID : {client_id}")
    print(f"Scopes    : {scopes}")
    print()

    flow = app.initiate_device_flow(scopes=scopes)
    if "user_code" not in flow:
        print("❌ Device flow failed to start.")
        print(flow)
        print()
        print("Azure checklist:")
        print("  1. App registration → Authentication → Allow public client flows = Yes")
        print("  2. Expose an API → set Application ID URI (api://<client-id>) + a scope")
        print("  3. Or set APP_ENTRA_SCOPE in .env to your exposed scope")
        sys.exit(1)

    print(flow["message"])
    print()
    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print("❌ Token acquisition failed.")
        print(f"   error             : {result.get('error')}")
        print(f"   error_description : {result.get('error_description')}")
        sys.exit(1)

    token = result["access_token"]
    claims = _decode_claims(token)
    aud = claims.get("aud")
    print("✅ Access token acquired.")
    print(f"   aud   : {aud}")
    print(f"   oid   : {claims.get('oid')}")
    print(f"   email : {claims.get('email') or claims.get('preferred_username')}")
    print(f"   name  : {claims.get('name')}")
    print()

    expected_aud = os.environ.get("APP_ENTRA_AUDIENCE") or client_id
    if aud and expected_aud not in (aud if isinstance(aud, list) else [aud]):
        # Fall back to id_token — its aud is usually the client_id.
        id_token = result.get("id_token")
        if id_token:
            id_claims = _decode_claims(id_token)
            print(
                "⚠️  access_token audience does not match this API "
                f"(got {aud!r}, expected {expected_aud!r})."
            )
            print("   Using id_token instead (aud should be your client id).")
            print(f"   id_token aud: {id_claims.get('aud')}")
            token = id_token
            print()
        else:
            print(
                "⚠️  Token audience may be rejected by the API. "
                "Set APP_ENTRA_AUDIENCE to match, or expose an API scope."
            )
            print()

    print("──── paste this token ────")
    print(token)
    print("──────────────────────────")
    print()
    print("Test:")
    print(f'  curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/me')
    print("Or: http://localhost:8000/docs → Authorize → Bearer <token>")


if __name__ == "__main__":
    main()
