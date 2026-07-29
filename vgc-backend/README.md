# VGC Binder — full backend API

FastAPI + SQLite backend for the browser app: accounts, collections, binders,
trade browsing, and messaging all live server-side. The client keeps only UI state.

## Run it

```bash
cd vgc-backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --reload
```

Interactive docs at <http://127.0.0.1:8000/docs>. The database is created on first
start as `vgc.db` beside the code; set `VGC_DB` to move it.

```bash
.venv/bin/python -m pytest -q     # 39 tests
```

## Auth model

Two tokens, issued together by `/auth/register` and `/auth/login`:

| Token | Form | Life | Purpose |
|---|---|---|---|
| `access_token` | HS256 JWT | 15 min | Sent as `Authorization: Bearer …` on every request; verified statelessly |
| `refresh_token` | opaque random | 30 days | Exchanged at `/auth/refresh` for new access tokens; revoked by `/auth/logout` |

Logout revokes the refresh token immediately; an already-issued access token keeps
working for at most its remaining 15 minutes. That window is the standard trade-off
of stateless access tokens — if it's unacceptable, shorten `ACCESS_TTL_SECONDS`.

The signing secret comes from `VGC_SECRET` (min 32 bytes, enforced at startup), or is
generated once and stored beside the database as `.jwt_secret` (mode 0600). Refresh
tokens are stored as SHA-256 hashes, so a leaked database contains no usable sessions.

Passwords are bcrypt (cost 12, `VGC_BCRYPT_COST` to change) over a SHA-256+base64
prehash — the standard fix for bcrypt's 72-byte limit, so long passphrases are fully
counted instead of silently truncated. There's a test proving bytes past 72 matter.

## Endpoints

All except `/health`, `/auth/register`, `/auth/login`, and `/auth/refresh` require a
bearer access token.

| Method | Path | Does |
|---|---|---|
| `POST` | `/auth/register` | Create account → token pair |
| `POST` | `/auth/login` | Credentials → token pair |
| `POST` | `/auth/refresh` | Refresh token → new access token |
| `POST` | `/auth/logout` | Revoke a refresh token |
| `GET` | `/me` | The signed-in account |
| `GET` | `/accounts` | Every other trainer |
| `GET` | `/pokemon` | Your collection |
| `POST` | `/pokemon` | Add an entry |
| `PUT` | `/pokemon/{id}` | Edit an entry |
| `DELETE` | `/pokemon/{id}` | Remove an entry (auto-unfiles it from binders) |
| `GET` | `/binders` | Your binders, with `pokemon_ids` |
| `POST` | `/binders` | Create a binder |
| `PUT` | `/binders/{id}` | Rename / description / tradeable flag |
| `DELETE` | `/binders/{id}` | Delete binder (Pokémon stay in the collection) |
| `PUT` | `/binders/{id}/pokemon` | Replace contents: `{"pokemon_ids": [...]}` |
| `GET` | `/trade/binders` | Other trainers' tradeable, non-empty binders with full Pokémon |
| `GET` | `/conversations` | Threads with preview and unread count |
| `GET` | `/messages/{peer_id}` | One thread, oldest first (`?limit=` ≤ 500) |
| `POST` | `/messages` | Send `{to, body, attachment?}` |
| `POST` | `/conversations/{peer_id}/read` | Mark that trainer's messages read |

## Access-control rules (all tested)

- Collections and binders are strictly per-account; editing another account's rows
  returns 404, indistinguishable from "doesn't exist".
- Filing another trainer's Pokémon into your binder is rejected (422).
- `/trade/binders` is the **only** cross-account read of collection data, and only
  binders explicitly flagged `tradeable` with at least one Pokémon ever appear there.
- Message threads are visible to their two participants only.
- A refresh token presented as an access token is rejected (`type` claim check).
- Login returns identical errors for unknown user vs. wrong password, and burns a
  bcrypt comparison either way — no account enumeration.

## Client conventions

- Timestamps are epoch milliseconds (`Date.now()` compatible).
- Species names are normalised to lowercase server-side.
- Sprite URLs must be `https://`.
- Message `attachment` is an arbitrary JSON blob — the existing frontend `clip`
  object can be sent unchanged.
- `Origin: null` is allowed by default so a `file://` page works. For production:
  `VGC_ORIGINS="https://binder.example.com"`.

## Frontend

`../self-hosted.html` is wired to this API: login/register gate, server-side
collection, binders, trade browsing, and messaging with unread counts. It keeps the
access token in memory and the refresh token in localStorage (so a browser restart
doesn't sign you out), retrying a 401 once through `/auth/refresh` before showing
the login screen. Its Export button downloads your account as JSON; Import
*adds* a backup's contents to your account and also understands the old
localStorage-era `vgc-binder` v1 export format. The pre-API standalone version is
preserved as `../demo.html`, and `../index.html` is the portfolio page.

## Before this faces the internet

Fine for local or trusted-network use as-is. A public deployment still needs:

- **HTTPS** — tokens travel in a header.
- **Rate limiting** on `/auth/login` and `/auth/refresh`.
- **Postgres** once concurrent writers are real; SQLite is single-writer.
- **Account deletion / password change** — neither exists yet.
- **Refresh-token rotation** — currently refresh tokens are long-lived and reusable;
  rotating on each `/auth/refresh` would shrink the stolen-token window further.
