"""End-to-end API tests. Each test gets a throwaway database.

    .venv/bin/python -m pytest -q
"""

import importlib
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    tmp = Path(tempfile.mkdtemp())
    monkeypatch.setenv("VGC_DB", str(tmp / "test.db"))
    monkeypatch.setenv("VGC_SECRET", "test-secret-not-for-production-0123456789")
    monkeypatch.setenv("VGC_BCRYPT_COST", "4")  # fast hashes; cost is not under test

    # Re-import so DB_PATH and the secret pick up the fresh environment.
    import db, auth, main
    importlib.reload(db)
    importlib.reload(auth)
    importlib.reload(main)

    with TestClient(main.app) as c:
        yield c


def register(client, username="ash", display="Ash", password="pikachu123"):
    r = client.post("/auth/register", json={
        "username": username, "display_name": display, "password": password,
    })
    assert r.status_code == 201, r.text
    return r.json()


def bearer(payload):
    """Auth header from a register/login response (or a raw token string)."""
    token = payload["access_token"] if isinstance(payload, dict) else payload
    return {"Authorization": f"Bearer {token}"}


# ─────────────────── accounts & registration ───────────────────

def test_register_returns_both_tokens_and_never_the_hash(client):
    data = register(client)
    assert data["access_token"] and data["refresh_token"]
    assert data["token_type"] == "bearer"
    assert data["account"]["username"] == "ash"
    assert "password" not in str(data)


def test_duplicate_username_is_rejected_case_insensitively(client):
    register(client, "ash")
    r = client.post("/auth/register", json={
        "username": "ASH", "display_name": "Impostor", "password": "another123",
    })
    assert r.status_code == 409


def test_short_password_rejected(client):
    r = client.post("/auth/register", json={
        "username": "bob", "display_name": "Bob", "password": "short",
    })
    assert r.status_code == 422


def test_long_passphrases_work_and_differ_past_bcrypt_72_bytes(client):
    """The SHA-256 prehash means bytes past 72 still count."""
    base = "x" * 72
    register(client, "bob", "Bob", password=base + "AAAA")

    ok = client.post("/auth/login", json={"username": "bob", "password": base + "AAAA"})
    assert ok.status_code == 200

    # Same first 72 bytes, different tail: raw bcrypt would accept this. We must not.
    bad = client.post("/auth/login", json={"username": "bob", "password": base + "BBBB"})
    assert bad.status_code == 401


def test_username_charset_enforced(client):
    r = client.post("/auth/register", json={
        "username": "bad name!", "display_name": "Bad", "password": "password123",
    })
    assert r.status_code == 422


def test_login_works_and_wrong_password_fails(client):
    register(client, "ash", password="pikachu123")

    ok = client.post("/auth/login", json={"username": "ash", "password": "pikachu123"})
    assert ok.status_code == 200 and ok.json()["access_token"]

    bad = client.post("/auth/login", json={"username": "ash", "password": "wrong1234"})
    assert bad.status_code == 401


def test_login_error_identical_for_unknown_user(client):
    """No account enumeration: same status and same message either way."""
    register(client, "ash", password="pikachu123")
    unknown = client.post("/auth/login", json={"username": "nobody", "password": "pikachu123"})
    wrong = client.post("/auth/login", json={"username": "ash", "password": "nope12345"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


# ─────────────────── token lifecycle ───────────────────

@pytest.mark.parametrize("path", ["/me", "/accounts", "/pokemon", "/binders",
                                  "/trade/binders", "/conversations", "/messages/1"])
def test_protected_endpoints_reject_anonymous(client, path):
    assert client.get(path).status_code == 401


def test_garbage_token_rejected(client):
    assert client.get("/me", headers=bearer("not-a-jwt")).status_code == 401


def test_expired_access_token_rejected(client):
    import auth
    data = register(client)
    real_ttl = auth.ACCESS_TTL_SECONDS
    auth.ACCESS_TTL_SECONDS = -10  # mint one already in the past
    try:
        stale = auth.make_access_token(data["account"]["id"])
    finally:
        auth.ACCESS_TTL_SECONDS = real_ttl
    r = client.get("/me", headers=bearer(stale))
    assert r.status_code == 401
    assert "expired" in r.json()["detail"].lower()


def test_token_signed_with_wrong_secret_rejected(client):
    import jwt as pyjwt
    from db import now_ms
    data = register(client)
    forged = pyjwt.encode(
        {"sub": str(data["account"]["id"]), "type": "access",
         "iat": now_ms() // 1000, "exp": now_ms() // 1000 + 900},
        "a-different-secret-also-32-bytes-long!", algorithm="HS256",
    )
    assert client.get("/me", headers=bearer(forged)).status_code == 401


def test_refresh_token_cannot_be_used_as_access_token(client):
    data = register(client)
    assert client.get("/me", headers=bearer(data["refresh_token"])).status_code == 401


def test_refresh_issues_a_working_access_token(client):
    data = register(client)
    r = client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 200
    assert client.get("/me", headers=bearer(r.json()["access_token"])).status_code == 200


def test_logout_revokes_refresh_but_access_lives_out_its_ttl(client):
    data = register(client)
    assert client.post("/auth/logout", json={"refresh_token": data["refresh_token"]}).status_code == 204

    # Refresh is dead immediately.
    r = client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 401

    # The stateless access token keeps working until its 15 minutes run out —
    # the documented trade-off of the two-token scheme.
    assert client.get("/me", headers=bearer(data)).status_code == 200


def test_expired_refresh_token_rejected(client):
    import db
    data = register(client)
    with db.connect() as conn:
        conn.execute("UPDATE refresh_tokens SET expires_at = ?", (db.now_ms() - 1000,))
    r = client.post("/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert r.status_code == 401


# ─────────────────── collection ───────────────────

MON = {"species": "Incineroar", "shiny": True, "mark": "Rare Mark",
       "origin_game": "Scarlet", "tera_type": "Water", "ball": "Beast Ball",
       "level": 50, "dex_id": 727, "art": "https://example.com/i.png"}


def test_pokemon_crud_roundtrip(client):
    ash = register(client)
    created = client.post("/pokemon", headers=bearer(ash), json=MON)
    assert created.status_code == 201
    mon = created.json()
    assert mon["species"] == "incineroar"  # normalised lowercase
    assert mon["shiny"] is True

    edited = client.put(f"/pokemon/{mon['id']}", headers=bearer(ash),
                        json={**MON, "nickname": "Cap", "level": 100})
    assert edited.status_code == 200
    assert edited.json()["nickname"] == "Cap"

    assert [p["nickname"] for p in client.get("/pokemon", headers=bearer(ash)).json()] == ["Cap"]

    assert client.delete(f"/pokemon/{mon['id']}", headers=bearer(ash)).status_code == 204
    assert client.get("/pokemon", headers=bearer(ash)).json() == []


def test_http_sprite_url_rejected(client):
    ash = register(client)
    r = client.post("/pokemon", headers=bearer(ash),
                    json={**MON, "art": "http://insecure.example.com/x.png"})
    assert r.status_code == 422


def test_cannot_edit_or_delete_someone_elses_pokemon(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    mon = client.post("/pokemon", headers=bearer(ash), json=MON).json()

    assert client.put(f"/pokemon/{mon['id']}", headers=bearer(misty),
                      json={**MON, "nickname": "Stolen"}).status_code == 404
    assert client.delete(f"/pokemon/{mon['id']}", headers=bearer(misty)).status_code == 404

    # Untouched.
    mine = client.get("/pokemon", headers=bearer(ash)).json()
    assert mine[0]["nickname"] == ""


def test_collections_are_per_account(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    client.post("/pokemon", headers=bearer(ash), json=MON)

    assert client.get("/pokemon", headers=bearer(misty)).json() == []


# ─────────────────── binders ───────────────────

def test_binder_crud_and_contents(client):
    ash = register(client)
    a = client.post("/pokemon", headers=bearer(ash), json=MON).json()
    b = client.post("/pokemon", headers=bearer(ash), json={**MON, "species": "miraidon"}).json()

    binder = client.post("/binders", headers=bearer(ash),
                         json={"name": "Legends", "tradeable": False}).json()
    assert binder["pokemon_ids"] == []

    filed = client.put(f"/binders/{binder['id']}/pokemon", headers=bearer(ash),
                       json={"pokemon_ids": [a["id"], b["id"]]})
    assert filed.status_code == 200
    assert sorted(filed.json()["pokemon_ids"]) == sorted([a["id"], b["id"]])

    renamed = client.put(f"/binders/{binder['id']}", headers=bearer(ash),
                         json={"name": "Restricted legends", "tradeable": True})
    assert renamed.json()["tradeable"] is True
    # Contents survive a metadata edit.
    assert sorted(renamed.json()["pokemon_ids"]) == sorted([a["id"], b["id"]])

    assert client.delete(f"/binders/{binder['id']}", headers=bearer(ash)).status_code == 204
    # Deleting the binder never deletes the Pokémon.
    assert len(client.get("/pokemon", headers=bearer(ash)).json()) == 2


def test_cannot_file_someone_elses_pokemon(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    ashs_mon = client.post("/pokemon", headers=bearer(ash), json=MON).json()
    binder = client.post("/binders", headers=bearer(misty), json={"name": "Sneaky"}).json()

    r = client.put(f"/binders/{binder['id']}/pokemon", headers=bearer(misty),
                   json={"pokemon_ids": [ashs_mon["id"]]})
    assert r.status_code == 422


def test_cannot_touch_someone_elses_binder(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    binder = client.post("/binders", headers=bearer(ash), json={"name": "Private"}).json()

    assert client.put(f"/binders/{binder['id']}", headers=bearer(misty),
                      json={"name": "Hijacked"}).status_code == 404
    assert client.delete(f"/binders/{binder['id']}", headers=bearer(misty)).status_code == 404


def test_deleting_pokemon_unfiles_it_from_binders(client):
    ash = register(client)
    mon = client.post("/pokemon", headers=bearer(ash), json=MON).json()
    binder = client.post("/binders", headers=bearer(ash), json={"name": "B"}).json()
    client.put(f"/binders/{binder['id']}/pokemon", headers=bearer(ash),
               json={"pokemon_ids": [mon["id"]]})

    client.delete(f"/pokemon/{mon['id']}", headers=bearer(ash))
    binders = client.get("/binders", headers=bearer(ash)).json()
    assert binders[0]["pokemon_ids"] == []


# ─────────────────── trade browsing ───────────────────

def _stock_misty(client, misty, tradeable=True):
    mon = client.post("/pokemon", headers=bearer(misty),
                      json={**MON, "species": "starmie"}).json()
    binder = client.post("/binders", headers=bearer(misty),
                         json={"name": "Water types", "tradeable": tradeable}).json()
    client.put(f"/binders/{binder['id']}/pokemon", headers=bearer(misty),
               json={"pokemon_ids": [mon["id"]]})
    return binder


def test_tradeable_binders_visible_with_pokemon(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    _stock_misty(client, misty, tradeable=True)

    seen = client.get("/trade/binders", headers=bearer(ash)).json()
    assert len(seen) == 1
    assert seen[0]["owner"]["username"] == "misty"
    assert [p["species"] for p in seen[0]["pokemon"]] == ["starmie"]


def test_private_binders_never_leak(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    _stock_misty(client, misty, tradeable=False)

    assert client.get("/trade/binders", headers=bearer(ash)).json() == []


def test_own_and_empty_tradeable_binders_excluded(client):
    ash = register(client, "ash", "Ash")
    _stock_misty(client, ash, tradeable=True)          # own binder
    misty = register(client, "misty", "Misty")
    client.post("/binders", headers=bearer(misty),      # tradeable but empty
                json={"name": "Empty", "tradeable": True})

    assert client.get("/trade/binders", headers=bearer(ash)).json() == []


# ─────────────────── messaging ───────────────────

def test_send_and_read_a_thread(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")

    sent = client.post("/messages", headers=bearer(ash), json={
        "to": misty["account"]["id"], "body": "Trade your Starmie?",
    })
    assert sent.status_code == 201

    thread = client.get(f"/messages/{ash['account']['id']}", headers=bearer(misty))
    assert [m["body"] for m in thread.json()] == ["Trade your Starmie?"]


def test_attachment_round_trips_as_json(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    clip = {"name": "Shiny legends", "count": 4}

    client.post("/messages", headers=bearer(ash),
                json={"to": misty["account"]["id"], "body": "", "attachment": clip})

    thread = client.get(f"/messages/{ash['account']['id']}", headers=bearer(misty)).json()
    assert thread[0]["attachment"] == clip


def test_empty_message_rejected(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    r = client.post("/messages", headers=bearer(ash),
                    json={"to": misty["account"]["id"], "body": "   "})
    assert r.status_code == 422


def test_cannot_message_yourself_or_nobody(client):
    ash = register(client, "ash", "Ash")
    assert client.post("/messages", headers=bearer(ash),
                       json={"to": ash["account"]["id"], "body": "hi"}).status_code == 400
    assert client.post("/messages", headers=bearer(ash),
                       json={"to": 9999, "body": "hi"}).status_code == 404


def test_third_party_cannot_read_someone_elses_thread(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    brock = register(client, "brock", "Brock")

    client.post("/messages", headers=bearer(ash),
                json={"to": misty["account"]["id"], "body": "secret trade terms"})

    assert client.get(f"/messages/{ash['account']['id']}", headers=bearer(brock)).json() == []
    assert client.get("/conversations", headers=bearer(brock)).json() == []


def test_conversation_list_previews_and_unread_lifecycle(client):
    ash = register(client, "ash", "Ash")
    misty = register(client, "misty", "Misty")
    for body in ["first", "second"]:
        client.post("/messages", headers=bearer(ash),
                    json={"to": misty["account"]["id"], "body": body})

    convs = client.get("/conversations", headers=bearer(misty)).json()
    assert convs[0]["last_message"] == "second"
    assert convs[0]["unread"] == 2

    marked = client.post(f"/conversations/{ash['account']['id']}/read", headers=bearer(misty))
    assert marked.json()["marked_read"] == 2
    assert client.get("/conversations", headers=bearer(misty)).json()[0]["unread"] == 0


def test_accounts_list_excludes_self(client):
    ash = register(client, "ash", "Ash")
    register(client, "misty", "Misty")
    names = [a["username"] for a in client.get("/accounts", headers=bearer(ash)).json()]
    assert names == ["misty"]
