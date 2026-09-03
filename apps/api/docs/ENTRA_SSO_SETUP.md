# Entra ID SSO setup — Digital Twin

How Microsoft Entra ID (Azure AD) is set up for **Digital Twin** user sign-in
and how the API validates those tokens.

This is **user SSO** (delegated tokens for humans), not client-credentials /
service-to-service Graph access. The API does **not** need a client secret
to validate JWTs.

---

## Overview

| Piece | Role |
|-------|------|
| **Entra ID** | Source of truth for identity (login, MFA). No passwords in our DB. |
| **Frontend (MSAL)** | Signs the user in; sends `Authorization: Bearer <jwt>` on API calls. |
| **Backend (FastAPI)** | Validates JWT (signature via JWKS, `iss`, `aud`, `exp`); JIT-provisions `core.users`. |
| **`GET /api/v1/me`** | Returns `{ id, name, email, role }` for the authenticated user. |

```
User → Entra (sign in) → FE gets JWT → API validates JWT → JIT user row → /me
```

---

## Prerequisites

- Access to [Azure Portal](https://portal.azure.com) under the Questkart tenant
- Permission to create an App Registration (admin consent may need Chandra Sir)
- Digital Twin API running locally (`uvicorn` from `apps/api`)
- Python 3.14+ with `uv sync` (includes `msal` for the token helper)

---

## Step 1 — Create a security group (optional but recommended)

Controls who can use the app when assignment is required.

1. **Azure Portal → Microsoft Entra ID → Groups → New group**
2. **Group type:** Security  
   **Group name:** `DigitalTwin`  
   **Membership type:** Assigned
3. Create the group.

Use the **Security** group for access control (not a Microsoft 365 group).

---

## Step 2 — Create the App Registration

1. **Azure Portal → App registrations → New registration**
2. **Name:** `DigitalTwin`
3. **Supported account types:** Single tenant (Questkart only)
4. **Redirect URI:**
   - For the **frontend SPA:** platform **Single-page application (SPA)**  
     e.g. `http://localhost:3000`
   - You can add more redirect URIs later for deployed environments
5. Register.

Notes:

- SPA is correct for the browser MSAL client (no client secret in the frontend).
- A **client secret is not required** for Digital Twin API JWT validation.
- Keep **Allow public client flows = Yes** (Authentication) if you use
  `scripts/get_dev_token.py` (device-code) for backend-only testing.

---

## Step 3 — Copy Client ID and Tenant ID

On the app **Overview** page:

| Azure field | Env var |
|-------------|---------|
| Application (client) ID | `APP_ENTRA_CLIENT_ID` |
| Directory (tenant) ID | `APP_ENTRA_TENANT_ID` |

Put them in `apps/api/.env` (never commit `.env`):

```env
APP_ENTRA_TENANT_ID=<directory-tenant-id>
APP_ENTRA_CLIENT_ID=<application-client-id>
# Optional if access-token aud is api://<client-id>:
# APP_ENTRA_AUDIENCE=api://<application-client-id>
```

---

## Step 4 — Expose an API scope (for production FE access tokens)

So the frontend can request an **access token** meant for this API
(`aud` = your app), not only a Microsoft Graph token.

1. App registration → **Expose an API**
2. Set **Application ID URI** to `api://<APP_ENTRA_CLIENT_ID>` (Accept if prompted)
3. **Add a scope**, e.g.:
   - Name: `access_as_user`
   - Who can consent: Admins and users
4. Full scope value: `api://<APP_ENTRA_CLIENT_ID>/access_as_user`

Frontend MSAL should request that scope (plus MSAL’s own OIDC handling).

For local backend testing before this is configured,
`scripts/get_dev_token.py` uses `User.Read` and prefers the **id_token**
(whose `aud` is the client id).

---

## Step 5 — API permissions (delegated)

1. **API permissions → Add a permission → Microsoft Graph → Delegated**
2. Add **`User.Read`** (usually present by default)
3. You do **not** need `Organization.Read.All` Application permission for SSO / `/me`

If the portal shows “Admin consent required” for a permission you added,
ask an admin (see Contact).

---

## Step 6 — Restrict who can sign in (recommended)

1. **Enterprise applications → DigitalTwin → Properties**
2. Set **Assignment required?** → **Yes** → Save
3. **Users and groups → Add user/group** → add the `DigitalTwin` security group
4. Add members to that security group under **Groups → DigitalTwin → Members**

---

## Step 7 — Configure and run the API

```bash
cd apps/api
cp .env.example .env   # fill APP_ENTRA_* and Postgres
uv sync
uvicorn app.main:app --reload
```

Confirm:

```bash
curl http://localhost:8000/health
```

---

## Step 8 — Test SSO against `GET /me` (backend)

```bash
cd apps/api
python scripts/get_dev_token.py
```

1. Open the device-login URL, enter the code, sign in with an allowed Entra user
2. Copy the printed token
3. Call:

```bash
curl -H "Authorization: Bearer <token>" http://localhost:8000/api/v1/me
```

Or: http://localhost:8000/docs → **Authorize** → paste token → `GET /api/v1/me`

**Expected 200:**

```json
{
  "id": "...",
  "name": "Shridhar K",
  "email": "you@questkart.in",
  "role": "operator"
}
```

First successful call **creates** the row in `core.users` (JIT). Later calls
update `last_login_at` only.

### Frontend contract (same endpoint)

```http
GET /api/v1/me
Authorization: Bearer <access_token_from_msal>
```

No request body. On **401**, clear session and sign in again.

---

## What the backend validates

Implemented in `app/core/auth.py` + `app/api/deps.py`:

1. Bearer header present
2. RS256 signature via Entra JWKS (`.../discovery/v2.0/keys`)
3. Claims: `iss`, `aud`, `exp` (and required `iat` / `sub`)
4. Identity from `oid` (or `sub`), `email` / `preferred_username`, `name`, `tid`
5. Upsert into `core.users` by `auth_provider_id = oid`

Issuer expected:

`https://login.microsoftonline.com/<TENANT_ID>/v2.0`

Audience expected:

`APP_ENTRA_AUDIENCE` if set, otherwise `APP_ENTRA_CLIENT_ID`

---

## What you do **not** need for this SSO path

| Skip | Why |
|------|-----|
| Client secret for the API | Validation uses public JWKS, not the secret |
| Client-credentials → Graph as the `/me` token | Graph tokens have wrong `aud`; `/me` rejects them |
| `Organization.Read.All` | Not used by Digital Twin auth |
| Hardcoding IDs/secrets in git | Use `apps/api/.env` only (gitignored) |

---

## Troubleshooting

| Symptom | Likely fix |
|---------|------------|
| `AADSTS500011` resource `api://...` not found | Complete **Step 4** (Expose an API), or test with default `User.Read` + id_token |
| MSAL error about reserved scopes | Do not pass `openid` / `profile` into device-flow scopes; MSAL adds them |
| API **401** | Wrong/expired token, or `aud`/`iss` mismatch — set `APP_ENTRA_AUDIENCE` if needed |
| API **500** `permission denied for schema core` | Grant DB user `USAGE` on schema `core` (+ table privileges) |
| User can authenticate in Entra but should not use the app | Enable assignment required + security group (**Step 6**) |
| Admin consent button greyed out | Contact Global Admin (below) |

---

## Security practices

- Store `APP_ENTRA_*` and DB credentials only in `.env` (already in `.gitignore`)
- Never commit client secrets or paste them into PRs/chat
- Prefer security groups + assignment required over open tenant access
- Rotate any secret that was ever shared

---

## Contact

For admin consent or Entra access issues:

- **Chandra Sir** — chandra@questkart.in  
- CC as needed: Sekar Periyasamy, Ravi J Gowda, Parameshwar G

---

*Digital Twin — Entra SSO (issue #16). Last updated: August 2026.*
