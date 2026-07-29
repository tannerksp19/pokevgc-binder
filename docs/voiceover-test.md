# VoiceOver test script — VGC Binder

Roughly 10 minutes. Safari gives the most representative result, since VoiceOver and
Safari share the same accessibility pipeline that most Mac screen reader users rely on.

## Before you start

**Toggle VoiceOver:** `Cmd + F5` (or triple-press Touch ID on machines that have it).
That same shortcut turns it off — worth practising once before you begin, because
VoiceOver captures most keystrokes while it runs.

**The keys you need:**

| Key | Does |
|---|---|
| `Ctrl + Option` | The "VO" modifier referenced below |
| `VO + →` / `VO + ←` | Move through the page one element at a time |
| `VO + Shift + ↓` | Step *into* a group (a card, a list) |
| `VO + Shift + ↑` | Step back *out* of a group |
| `VO + Space` | Activate whatever VoiceOver is on |
| `VO + U` | Open the rotor; `←`/`→` switch category, `↑`/`↓` browse, `Esc` closes |
| `Ctrl` | Interrupt speech |

Open `vgc-binder.html` in Safari, then press `VO + U` and check the rotor works before
starting. If the app has no data yet, add two or three Pokémon first — several checks
below need entries to read.

---

## 1. Getting in

| # | Do | Expect |
|---|---|---|
| 1.1 | Press `Tab` once from the top of the page | "Skip to main content, link" |
| 1.2 | Press `Return` on it | Focus jumps past the header into the main area |
| 1.3 | `VO + U` → rotor → **Landmarks** | banner, navigation, main, and a search landmark |
| 1.4 | Rotor → **Headings** | "VGC Binder" at level 1, then "Collection" at level 2, then one level‑3 heading per Pokémon — no skipped levels |

## 2. Section tabs

| # | Do | Expect |
|---|---|---|
| 2.1 | Tab to the section tabs | "Collection, 4 items, selected, tab, 1 of 3" — the count is spoken, not just shown |
| 2.2 | Press `→` | Moves to "Binders" **and** switches the panel. Focus should not escape the tab strip |
| 2.3 | Press `End`, then `Home` | Jumps to Trade, then back to Collection |
| 2.4 | Press `Tab` from a tab | Moves into the panel — the unselected tabs are skipped, not tabbed through |

## 3. The collection grid

| # | Do | Expect |
|---|---|---|
| 3.1 | Navigate to the stat tiles | "4 catalogued", "2 shiny" — as coherent phrases, **not** "4Catalogued" |
| 3.2 | `VO + →` onto a Pokémon card | Announced as an article/group named for the Pokémon |
| 3.3 | `VO + Shift + ↓` into a card | Sprite described, then the name as a heading, then "Dex 727" — the "#" and "·" should **not** be read literally |
| 3.4 | Continue through the card | Provenance reads as a list: origin, mark, ball, "Tera Water" — Tera type is **spoken**, never colour-only |
| 3.5 | Reach the buttons | "Edit incineroar" and "Remove incineroar from collection" — never a bare "Edit" |
| 3.6 | Rotor → **Form controls** | Every button names its Pokémon; no two entries sound alike |

## 4. Filtering (live regions)

| # | Do | Expect |
|---|---|---|
| 4.1 | Tab to the search box | "Search species or nickname, edit text" |
| 4.2 | Type a few letters | Focus **stays** in the box; the result count is announced without you moving |
| 4.3 | Toggle "Shiny only" | Named correctly as a checkbox, and the new count is announced |

## 5. Dialogs — the highest-risk area

| # | Do | Expect |
|---|---|---|
| 5.1 | Activate "Catalogue a Pokémon" | Announced as a dialog, titled "Catalogue a Pokémon", with its description read |
| 5.2 | `Tab` repeatedly, well past the last button | Focus **cycles inside the dialog**. If you ever land on the page behind it, that's a failure |
| 5.3 | `Shift + Tab` from the first control | Wraps to the last control |
| 5.4 | Tab through the fields | Every field has a real label; Species says "required" |
| 5.5 | Leave Species empty, press Save | The error is **spoken immediately**, focus lands on Species, and it reports as invalid |
| 5.6 | Press `Esc` | Dialog closes and focus returns to "Catalogue a Pokémon" — not to the top of the page |
| 5.7 | Open a card's Remove, listen, then choose "Keep it" | The consequence is stated (including which binders it affects) before you confirm |

## 6. Trade and messages

| # | Do | Expect |
|---|---|---|
| 6.1 | Trade tab → the two sub-tabs | Behave as a tab strip, arrow keys included |
| 6.2 | Browse view | Each button says who and what: "Ask Nadia about Shiny restricted legends" |
| 6.3 | Open a conversation | The thread is a labelled list; each message says who sent it before the text |
| 6.4 | Send a message | The new message is announced without you navigating to it |
| 6.5 | Send an empty message | Refusal is spoken, focus stays in the input |

## 7. Export / import

| # | Do | Expect |
|---|---|---|
| 7.1 | Tab to Export | "Export all trainers and collections to a file" |
| 7.2 | Navigate the whole page with `VO + →` to the very end | You should **never** hit a stray "Choose file" control — the file input is deliberately hidden |
| 7.3 | Activate Import, pick an exported file | The confirmation dialog announces its title and the counts it's about to replace |

## 8. Zoom and contrast

| # | Do | Expect |
|---|---|---|
| 8.1 | `Cmd +` to 200% | Nothing clipped or overlapping; no sideways scrolling of the page |
| 8.2 | System Settings → Appearance → Dark | Everything stays legible; the accent still reads |
| 8.3 | System Settings → Accessibility → Display → Increase contrast | Borders thicken, nothing disappears |

---

## Recording what you find

For anything that fails, note **where**, **what you heard**, and **what you expected**.
The wording matters more than the pass/fail — "it said 'four-catalogued' as one word"
is fixable; "the stats sounded wrong" isn't.

Known limits, so you don't chase them as bugs:

- Sprite images are described only by species name. That is the meaningful content;
  the art itself carries nothing extra.
- The foil sleeve and the Tera gem are decorative. Both facts are also written out as
  text ("SHINY", "Tera Water"), so nothing is conveyed by colour alone.
- Species names are PokéAPI slugs, so VoiceOver reads "urshifu-rapid-strike" with the
  hyphens as pauses. Give an entry a nickname if you want it read more naturally.
