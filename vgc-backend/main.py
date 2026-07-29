"""VGC Binder — accounts, collections, binders, and messaging API.

Run:  uvicorn main:app --reload
Docs: http://127.0.0.1:8000/docs

Auth is a two-token scheme: a 15-minute JWT access token for requests, and a
30-day opaque refresh token exchanged at /auth/refresh and revoked at /auth/logout.
"""

import json
import os
import sqlite3
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

from auth import (
    ACCESS_TTL_SECONDS,
    consume_refresh_token,
    current_account,
    hash_password,
    issue_refresh_token,
    make_access_token,
    revoke_refresh_token,
    verify_password,
)
from db import connect, init_db, now_ms


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(title="VGC Binder API", version="2.0", lifespan=lifespan)

# A page opened from disk sends `Origin: null`, so that literal string is needed
# for the file:// frontend. Override for a real deployment:
#   VGC_ORIGINS="https://binder.example.com"
_origins = os.environ.get("VGC_ORIGINS", "null,http://localhost:8000,http://127.0.0.1:8000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
    allow_credentials=False,  # auth rides in the Authorization header, not cookies
)


# ─────────────────────────── schemas ───────────────────────────

class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    display_name: str = Field(min_length=1, max_length=40)
    password: str = Field(min_length=8, max_length=1024)

    @field_validator("username")
    @classmethod
    def _clean_username(cls, v: str) -> str:
        v = v.strip()
        if not all(c.isalnum() or c in "_-" for c in v):
            raise ValueError("username may contain only letters, numbers, hyphen and underscore")
        return v


class LoginIn(BaseModel):
    username: str
    password: str


class RefreshIn(BaseModel):
    refresh_token: str


class AccountOut(BaseModel):
    id: int
    username: str
    display_name: str
    created_at: int


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TTL_SECONDS
    account: AccountOut


class AccessOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = ACCESS_TTL_SECONDS


class PokemonIn(BaseModel):
    species: str = Field(min_length=1, max_length=64)
    nickname: str = Field(default="", max_length=40)
    shiny: bool = False
    mark: str = Field(default="", max_length=40)
    ribbon: str = Field(default="", max_length=64)
    origin_game: str = Field(default="", max_length=40)
    tera_type: str = Field(default="", max_length=20)
    ball: str = Field(default="", max_length=40)
    level: Optional[int] = Field(default=None, ge=1, le=100)
    nature: str = Field(default="", max_length=20)
    ability: str = Field(default="", max_length=40)
    ot: str = Field(default="", max_length=40)
    language: str = Field(default="", max_length=20)
    notes: str = Field(default="", max_length=2000)
    dex_id: Optional[int] = Field(default=None, ge=1)
    art: Optional[str] = Field(default=None, max_length=300)
    art_shiny: Optional[str] = Field(default=None, max_length=300)

    @field_validator("species")
    @classmethod
    def _clean_species(cls, v: str) -> str:
        return v.strip().lower()

    @field_validator("art", "art_shiny")
    @classmethod
    def _https_only(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith("https://"):
            raise ValueError("sprite URLs must be https")
        return v


class PokemonOut(PokemonIn):
    id: int
    account_id: int
    created_at: int


class BinderIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    description: str = Field(default="", max_length=500)
    tradeable: bool = False


class BinderOut(BinderIn):
    id: int
    account_id: int
    created_at: int
    pokemon_ids: List[int]


class BinderContentsIn(BaseModel):
    pokemon_ids: List[int] = Field(max_length=500)


class TradeBinderOut(BaseModel):
    id: int
    name: str
    description: str
    owner: AccountOut
    pokemon: List[PokemonOut]


class MessageIn(BaseModel):
    to: int = Field(description="Recipient account id")
    body: str = Field(default="", max_length=2000)
    attachment: Optional[Dict[str, Any]] = Field(
        default=None, description="Opaque client blob, e.g. a binder reference"
    )

    @field_validator("body")
    @classmethod
    def _trim(cls, v: str) -> str:
        return v.strip()


class MessageOut(BaseModel):
    id: int
    sender_id: int
    recipient_id: int
    body: str
    attachment: Optional[Dict[str, Any]]
    created_at: int
    read_at: Optional[int]


class ConversationOut(BaseModel):
    account: AccountOut
    last_message: Optional[str]
    last_at: Optional[int]
    unread: int


# ─────────────────────────── row mappers ───────────────────────────

def _account_out(row: sqlite3.Row) -> AccountOut:
    return AccountOut(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        created_at=row["created_at"],
    )


def _pokemon_out(row: sqlite3.Row) -> PokemonOut:
    return PokemonOut(
        id=row["id"], account_id=row["account_id"], species=row["species"],
        nickname=row["nickname"], shiny=bool(row["shiny"]), mark=row["mark"],
        ribbon=row["ribbon"], origin_game=row["origin_game"], tera_type=row["tera_type"],
        ball=row["ball"], level=row["level"], nature=row["nature"], ability=row["ability"],
        ot=row["ot"], language=row["language"], notes=row["notes"], dex_id=row["dex_id"],
        art=row["art"], art_shiny=row["art_shiny"], created_at=row["created_at"],
    )


def _binder_out(conn: sqlite3.Connection, row: sqlite3.Row) -> BinderOut:
    ids = [r["pokemon_id"] for r in conn.execute(
        "SELECT pokemon_id FROM binder_pokemon WHERE binder_id = ? ORDER BY pokemon_id",
        (row["id"],),
    )]
    return BinderOut(
        id=row["id"], account_id=row["account_id"], name=row["name"],
        description=row["description"], tradeable=bool(row["tradeable"]),
        created_at=row["created_at"], pokemon_ids=ids,
    )


def _message_out(row: sqlite3.Row) -> MessageOut:
    return MessageOut(
        id=row["id"], sender_id=row["sender_id"], recipient_id=row["recipient_id"],
        body=row["body"], created_at=row["created_at"], read_at=row["read_at"],
        attachment=json.loads(row["attachment"]) if row["attachment"] else None,
    )


def _issue_pair(conn: sqlite3.Connection, account: sqlite3.Row) -> TokenOut:
    return TokenOut(
        access_token=make_access_token(account["id"]),
        refresh_token=issue_refresh_token(conn, account["id"]),
        account=_account_out(account),
    )


def _own_binder(conn: sqlite3.Connection, binder_id: int, account_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM binders WHERE id = ?", (binder_id,)).fetchone()
    # 404 for both "doesn't exist" and "not yours" — no probing other people's binder ids.
    if row is None or row["account_id"] != account_id:
        raise HTTPException(status_code=404, detail="No such binder")
    return row


# ─────────────────────────── auth ───────────────────────────

@app.post("/auth/register", response_model=TokenOut, status_code=201)
def register(payload: RegisterIn) -> TokenOut:
    with connect() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO accounts (username, display_name, password_hash, created_at) VALUES (?,?,?,?)",
                (payload.username, payload.display_name.strip(),
                 hash_password(payload.password), now_ms()),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="That username is taken")

        account = conn.execute(
            "SELECT id, username, display_name, created_at FROM accounts WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone()
        return _issue_pair(conn, account)


@app.post("/auth/login", response_model=TokenOut)
def login(payload: LoginIn) -> TokenOut:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM accounts WHERE username = ?", (payload.username.strip(),)
        ).fetchone()

        # Same error and roughly the same cost whether the account exists or not.
        if not verify_password(payload.password, row["password_hash"] if row else None):
            raise HTTPException(status_code=401, detail="Wrong username or password")

        return _issue_pair(conn, row)


@app.post("/auth/refresh", response_model=AccessOut)
def refresh(payload: RefreshIn) -> AccessOut:
    """Trade a live refresh token for a fresh access token."""
    with connect() as conn:
        account_id = consume_refresh_token(conn, payload.refresh_token)
    return AccessOut(access_token=make_access_token(account_id))


@app.post("/auth/logout", status_code=204)
def logout(payload: RefreshIn) -> None:
    """Revoke the refresh token. The access token simply expires within 15 minutes."""
    with connect() as conn:
        revoke_refresh_token(conn, payload.refresh_token)


@app.get("/me", response_model=AccountOut)
def me(account: sqlite3.Row = Depends(current_account)) -> AccountOut:
    return _account_out(account)


# ─────────────────────────── accounts ───────────────────────────

@app.get("/accounts", response_model=List[AccountOut])
def list_accounts(account: sqlite3.Row = Depends(current_account)) -> List[AccountOut]:
    """Every other trainer, so the client can pick someone to message."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, username, display_name, created_at FROM accounts WHERE id <> ? ORDER BY display_name",
            (account["id"],),
        ).fetchall()
    return [_account_out(r) for r in rows]


# ─────────────────────────── collection ───────────────────────────

@app.get("/pokemon", response_model=List[PokemonOut])
def list_pokemon(account: sqlite3.Row = Depends(current_account)) -> List[PokemonOut]:
    """Your whole collection. Filtering stays client-side, same as before."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM pokemon WHERE account_id = ? ORDER BY id", (account["id"],)
        ).fetchall()
    return [_pokemon_out(r) for r in rows]


@app.post("/pokemon", response_model=PokemonOut, status_code=201)
def add_pokemon(payload: PokemonIn, account: sqlite3.Row = Depends(current_account)) -> PokemonOut:
    fields = payload.model_dump()
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO pokemon (account_id, species, nickname, shiny, mark, ribbon,
                                    origin_game, tera_type, ball, level, nature, ability,
                                    ot, language, notes, dex_id, art, art_shiny, created_at)
               VALUES (:account_id, :species, :nickname, :shiny, :mark, :ribbon,
                       :origin_game, :tera_type, :ball, :level, :nature, :ability,
                       :ot, :language, :notes, :dex_id, :art, :art_shiny, :created_at)""",
            {**fields, "account_id": account["id"], "shiny": int(fields["shiny"]), "created_at": now_ms()},
        )
        row = conn.execute("SELECT * FROM pokemon WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _pokemon_out(row)


@app.put("/pokemon/{pokemon_id}", response_model=PokemonOut)
def edit_pokemon(
    payload: PokemonIn,
    pokemon_id: int = Path(ge=1),
    account: sqlite3.Row = Depends(current_account),
) -> PokemonOut:
    fields = payload.model_dump()
    with connect() as conn:
        cur = conn.execute(
            """UPDATE pokemon SET species=:species, nickname=:nickname, shiny=:shiny,
                   mark=:mark, ribbon=:ribbon, origin_game=:origin_game, tera_type=:tera_type,
                   ball=:ball, level=:level, nature=:nature, ability=:ability, ot=:ot,
                   language=:language, notes=:notes, dex_id=:dex_id, art=:art, art_shiny=:art_shiny
             WHERE id=:id AND account_id=:account_id""",
            {**fields, "shiny": int(fields["shiny"]), "id": pokemon_id, "account_id": account["id"]},
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="No such Pokémon")
        row = conn.execute("SELECT * FROM pokemon WHERE id = ?", (pokemon_id,)).fetchone()
    return _pokemon_out(row)


@app.delete("/pokemon/{pokemon_id}", status_code=204)
def delete_pokemon(
    pokemon_id: int = Path(ge=1), account: sqlite3.Row = Depends(current_account)
) -> None:
    """Removes the entry; the binder_pokemon cascade unfiles it everywhere."""
    with connect() as conn:
        cur = conn.execute(
            "DELETE FROM pokemon WHERE id = ? AND account_id = ?", (pokemon_id, account["id"])
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="No such Pokémon")


# ─────────────────────────── binders ───────────────────────────

@app.get("/binders", response_model=List[BinderOut])
def list_binders(account: sqlite3.Row = Depends(current_account)) -> List[BinderOut]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM binders WHERE account_id = ? ORDER BY id", (account["id"],)
        ).fetchall()
        return [_binder_out(conn, r) for r in rows]


@app.post("/binders", response_model=BinderOut, status_code=201)
def create_binder(payload: BinderIn, account: sqlite3.Row = Depends(current_account)) -> BinderOut:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO binders (account_id, name, description, tradeable, created_at) VALUES (?,?,?,?,?)",
            (account["id"], payload.name.strip(), payload.description.strip(),
             int(payload.tradeable), now_ms()),
        )
        row = conn.execute("SELECT * FROM binders WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _binder_out(conn, row)


@app.put("/binders/{binder_id}", response_model=BinderOut)
def edit_binder(
    payload: BinderIn,
    binder_id: int = Path(ge=1),
    account: sqlite3.Row = Depends(current_account),
) -> BinderOut:
    with connect() as conn:
        _own_binder(conn, binder_id, account["id"])
        conn.execute(
            "UPDATE binders SET name=?, description=?, tradeable=? WHERE id=?",
            (payload.name.strip(), payload.description.strip(), int(payload.tradeable), binder_id),
        )
        row = conn.execute("SELECT * FROM binders WHERE id = ?", (binder_id,)).fetchone()
        return _binder_out(conn, row)


@app.delete("/binders/{binder_id}", status_code=204)
def delete_binder(
    binder_id: int = Path(ge=1), account: sqlite3.Row = Depends(current_account)
) -> None:
    """Deletes the binder only — the Pokémon inside stay in the collection."""
    with connect() as conn:
        _own_binder(conn, binder_id, account["id"])
        conn.execute("DELETE FROM binders WHERE id = ?", (binder_id,))


@app.put("/binders/{binder_id}/pokemon", response_model=BinderOut)
def set_binder_contents(
    payload: BinderContentsIn,
    binder_id: int = Path(ge=1),
    account: sqlite3.Row = Depends(current_account),
) -> BinderOut:
    """Replace the binder's contents. Only your own Pokémon can be filed."""
    wanted = sorted(set(payload.pokemon_ids))
    with connect() as conn:
        row = _own_binder(conn, binder_id, account["id"])

        if wanted:
            placeholders = ",".join("?" for _ in wanted)
            owned = {r["id"] for r in conn.execute(
                f"SELECT id FROM pokemon WHERE account_id = ? AND id IN ({placeholders})",
                [account["id"], *wanted],
            )}
            foreign = [i for i in wanted if i not in owned]
            if foreign:
                raise HTTPException(
                    status_code=422,
                    detail=f"Not in your collection: {foreign}",
                )

        conn.execute("DELETE FROM binder_pokemon WHERE binder_id = ?", (binder_id,))
        conn.executemany(
            "INSERT INTO binder_pokemon (binder_id, pokemon_id) VALUES (?,?)",
            [(binder_id, pid) for pid in wanted],
        )
        return _binder_out(conn, row)


# ─────────────────────────── trade browsing ───────────────────────────

@app.get("/trade/binders", response_model=List[TradeBinderOut])
def tradeable_binders(account: sqlite3.Row = Depends(current_account)) -> List[TradeBinderOut]:
    """Every other trainer's tradeable, non-empty binders — Pokémon included.

    This is the one place another account's data is exposed, and only rows the
    owner explicitly flagged tradeable ever appear.
    """
    with connect() as conn:
        binders = conn.execute(
            """SELECT b.*, a.username, a.display_name, a.created_at AS acct_created
                 FROM binders b JOIN accounts a ON a.id = b.account_id
                WHERE b.tradeable = 1 AND b.account_id <> ?
                  AND EXISTS (SELECT 1 FROM binder_pokemon bp WHERE bp.binder_id = b.id)
                ORDER BY a.display_name, b.name""",
            (account["id"],),
        ).fetchall()

        out = []
        for b in binders:
            held = conn.execute(
                """SELECT p.* FROM pokemon p
                     JOIN binder_pokemon bp ON bp.pokemon_id = p.id
                    WHERE bp.binder_id = ? ORDER BY p.id""",
                (b["id"],),
            ).fetchall()
            out.append(TradeBinderOut(
                id=b["id"], name=b["name"], description=b["description"],
                owner=AccountOut(
                    id=b["account_id"], username=b["username"],
                    display_name=b["display_name"], created_at=b["acct_created"],
                ),
                pokemon=[_pokemon_out(p) for p in held],
            ))
    return out


# ─────────────────────────── messaging ───────────────────────────

@app.get("/conversations", response_model=List[ConversationOut])
def conversations(account: sqlite3.Row = Depends(current_account)) -> List[ConversationOut]:
    """One row per trainer you've exchanged messages with, newest first."""
    me_id = account["id"]
    with connect() as conn:
        rows = conn.execute(
            """
            WITH mine AS (
                SELECT CASE WHEN sender_id = :me THEN recipient_id ELSE sender_id END AS peer_id,
                       id, body, attachment, created_at, read_at, recipient_id
                  FROM messages
                 WHERE sender_id = :me OR recipient_id = :me
            )
            SELECT p.peer_id,
                   a.username,
                   a.display_name,
                   a.created_at AS peer_created_at,
                   MAX(p.created_at) AS last_at,
                   (SELECT body FROM mine WHERE peer_id = p.peer_id ORDER BY id DESC LIMIT 1) AS last_body,
                   (SELECT attachment FROM mine WHERE peer_id = p.peer_id ORDER BY id DESC LIMIT 1) AS last_attachment,
                   SUM(CASE WHEN p.recipient_id = :me AND p.read_at IS NULL THEN 1 ELSE 0 END) AS unread
              FROM mine p
              JOIN accounts a ON a.id = p.peer_id
             GROUP BY p.peer_id
             ORDER BY last_at DESC
            """,
            {"me": me_id},
        ).fetchall()

    out = []
    for r in rows:
        preview = r["last_body"] or ("Shared a binder" if r["last_attachment"] else "")
        out.append(ConversationOut(
            account=AccountOut(
                id=r["peer_id"], username=r["username"],
                display_name=r["display_name"], created_at=r["peer_created_at"],
            ),
            last_message=preview,
            last_at=r["last_at"],
            unread=r["unread"] or 0,
        ))
    return out


@app.get("/messages/{peer_id}", response_model=List[MessageOut])
def thread(
    peer_id: int = Path(ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    account: sqlite3.Row = Depends(current_account),
) -> List[MessageOut]:
    """The conversation with one trainer, oldest first. Only your own threads are visible."""
    me_id = account["id"]
    if peer_id == me_id:
        raise HTTPException(status_code=400, detail="No thread with yourself")

    with connect() as conn:
        rows = conn.execute(
            """SELECT * FROM messages
                WHERE (sender_id = :me AND recipient_id = :peer)
                   OR (sender_id = :peer AND recipient_id = :me)
                ORDER BY id DESC LIMIT :limit""",
            {"me": me_id, "peer": peer_id, "limit": limit},
        ).fetchall()

    return [_message_out(r) for r in reversed(rows)]


@app.post("/messages", response_model=MessageOut, status_code=201)
def send_message(payload: MessageIn, account: sqlite3.Row = Depends(current_account)) -> MessageOut:
    me_id = account["id"]
    if payload.to == me_id:
        raise HTTPException(status_code=400, detail="You can't message yourself")
    if not payload.body and payload.attachment is None:
        raise HTTPException(status_code=422, detail="Message needs a body or an attachment")

    with connect() as conn:
        if conn.execute("SELECT 1 FROM accounts WHERE id = ?", (payload.to,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="No such trainer")

        cur = conn.execute(
            "INSERT INTO messages (sender_id, recipient_id, body, attachment, created_at) VALUES (?,?,?,?,?)",
            (me_id, payload.to, payload.body,
             json.dumps(payload.attachment) if payload.attachment is not None else None,
             now_ms()),
        )
        row = conn.execute("SELECT * FROM messages WHERE id = ?", (cur.lastrowid,)).fetchone()

    return _message_out(row)


@app.post("/conversations/{peer_id}/read")
def mark_read(
    peer_id: int = Path(ge=1), account: sqlite3.Row = Depends(current_account)
) -> Dict[str, int]:
    """Mark everything that trainer sent you as read."""
    with connect() as conn:
        cur = conn.execute(
            "UPDATE messages SET read_at = ? WHERE recipient_id = ? AND sender_id = ? AND read_at IS NULL",
            (now_ms(), account["id"], peer_id),
        )
        return {"marked_read": cur.rowcount}


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}
