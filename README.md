# VGC Binder

A collection tracker and trading catalogue for competitive Pokémon players. Records
provenance — origin game, ball, marks, ribbons, original trainer — groups entries into
binders, and lets players publish a binder as tradeable so others can browse it and
open a trade negotiation.

Sprites and species data come from [PokéAPI](https://pokeapi.co).

**Live:** <https://tannerksp19.github.io/pokevgc-binder/>

## Two versions

| File | Storage | Accounts | Runs from |
|---|---|---|---|
| [`demo.html`](demo.html) | Browser localStorage | Simulated locally | Opens straight from disk — no setup |
| [`self-hosted.html`](self-hosted.html) | Server (SQLite) | Real, with login | Needs the backend running |

`demo.html` is what the live site links to, so the published page works with no
infrastructure. `self-hosted.html` is the same app rebuilt against the API in
[`vgc-backend/`](vgc-backend/): real accounts, cross-device messaging, and binders
shared between separate users.

`index.html` is the portfolio page, not the app.

## Running the self-hosted version

```bash
cd vgc-backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn main:app --reload
```

Then open `self-hosted.html` in a browser. Interactive API docs at
<http://127.0.0.1:8000/docs>. See [`vgc-backend/README.md`](vgc-backend/README.md)
for the endpoint list and design notes.

Tests:

```bash
cd vgc-backend && .venv/bin/python -m pytest -q
```

## What's in the backend

FastAPI over SQLite. Accounts use bcrypt with a SHA-256 prehash, so passphrases past
bcrypt's 72-byte limit aren't silently truncated. Auth is a two-token scheme:
short-lived HS256 JWT access tokens plus revocable opaque refresh tokens stored only as
hashes, which keeps logout meaningful. Collections, binders, tradeable-binder browsing,
and messaging with unread tracking are all server-side, covered by 39 end-to-end tests.

## Accessibility

Both frontends target WCAG 2.1 AA, verified by measuring contrast across every rendered
text node in light and dark themes rather than by inspection. Keyboard support includes
a roving-tabindex tab strip and focus-trapped dialogs that restore focus on close. A
manual screen reader pass has not been done — [`docs/voiceover-test.md`](docs/voiceover-test.md)
is a script for running one.

## Note

Unaffiliated fan project. Pokémon is a trademark of Nintendo, Creatures Inc., and Game Freak.
