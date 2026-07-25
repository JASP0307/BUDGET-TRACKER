# Cualto — logo brief

Self-contained context for designing this product's logo in another tool.
Everything needed is in this file; no repo access required.

**The palette and the typefaces are locked.** They are already shipping in the
live product and are not open for redesign here. Design the mark to live inside
them.

---

## 1. What to produce

| File                             | What it is                        | Notes                                                                      |
| -------------------------------- | --------------------------------- | -------------------------------------------------------------------------- |
| `logo-mark.svg`                  | The symbol alone, no text         | **Must read at 20×20 px.** This is the critical one — see §4.              |
| `logo-lockup.svg`                | Mark + wordmark, horizontal       | Used in the app header and on the login screen                             |
| `logo-mono.svg`                  | Single-colour version of the mark | One flat colour, no gradients; used where only `currentColor` is available |
| `favicon.svg` + `favicon-32.png` | Browser tab                       | Derived from the mark                                                      |
| `icon-192.png`, `icon-512.png`   | PWA install icons                 | Also supply a **maskable** variant with safe-zone padding                  |
| `apple-touch-icon.png` (180×180) | iOS home screen                   | Opaque background, no transparency                                         |

Vector source (SVG) is required, not just raster. Transparent background except
where noted. There is currently **no logo, favicon, or app icon of any kind** —
these will be the product's first image assets, so nothing needs to be matched
or migrated.

---

## 2. What the product is

Cualto is a **personal budget tracker for the Dominican Republic**. Users forward
their bank's card-notification emails to a private address; the app parses each
one, categorises the spend, and shows what's left to spend this month. It sends
Telegram alerts when a category runs low or goes over.

- **Audience:** ordinary people managing a household budget, not finance
  professionals. Not a trading app, not a crypto app, not enterprise fintech.
- **Language:** Spanish is the default; English is a toggle. Amounts are
  Dominican pesos, written `RD$`.
- **Platform:** mobile-first web app people open on a phone.
- **Emotional register:** the product's job is to answer "can I afford this?"
  calmly. It should feel reassuring and plain-spoken — not alarmist, not
  gamified, not luxurious.

### The name

The product name is **Cualto** and the domain is **cualtoapp.com**.

The in-product wordmark now reads **Cualto**, set in Instrument Sans 700.

The mark beside it is still the placeholder `RD$` chip described in §4 — that is
the slot this brief's `logo-mark.svg` replaces.

---

## 3. Locked palette

The app ships **light and dark themes**. The mark must work in both. Every value
below is live in production.

### Light theme

| Role | Hex | Where the mark meets it |
|---|---|---|
| `bg` page background | `#F2F4F1` | off-white, slightly green-grey |
| `card` raised surface | `#FFFFFF` | header bar sits on this |
| `line` borders | `#DEE5DF` | |
| `ink` primary text | `#182420` | near-black, green-shifted |
| `muted` secondary text | `#5C6B63` | |
| **`brand` accent** | **`#0E6B4A`** | deep green — the identity colour |
| `good` under budget | `#0E6B4A` | |
| `warn` near limit | `#9A6700` | amber |
| `bad` over budget | `#B3372E` | red |
| `btn-ink` text on brand fill | `#FFFFFF` | |

### Dark theme

| Role | Hex | |
|---|---|---|
| `bg` page background | `#101613` | near-black, green-shifted |
| `card` raised surface | `#18211C` | header bar sits on this |
| `line` borders | `#27332C` | |
| `ink` primary text | `#E6ECE7` | |
| `muted` secondary text | `#8FA096` | |
| **`brand` accent** | **`#4CB98A`** | lighter mint-green |
| `good` under budget | `#4CB98A` | |
| `warn` near limit | `#D9A03C` | |
| `bad` over budget | `#E06056` | |
| `btn-ink` text on brand fill | `#0E1512` | |

**The identity is green.** Note the brand green is *not* the same hex in both
themes — it lightens in dark mode so it stays legible on a near-black ground.
A logo that hard-codes `#0E6B4A` will look muddy in dark mode; either supply two
colourways or design a mark that can take `currentColor`.

Amber and red are **reserved for budget status** (near limit / over limit). Avoid
building them into the identity — a red logo would read as a warning state.

---

## 4. The hard constraint: it must survive at 20 px

In the app header the logo sits as a small mark plus a text wordmark. **On
screens narrower than 430 px the wordmark is hidden and only the mark
remains** — and this is a mobile-first product, so that is the common case, not
an edge case.

Consequences:

- The mark must be identifiable at roughly **20×20 px** with no accompanying text.
- No fine linework, no detail that closes up, no more than ~2 tones.
- Test it at 16, 20 and 24 px before considering it done. If it only works at
  128 px, it is the wrong mark for this product.

### The slot it drops into

The mark currently occupies a small rounded chip with these properties, which
you can either match or deliberately replace:

- background: the brand green; foreground: the card colour (a **knockout** —
  light shape on green in light mode, dark shape on mint in dark mode)
- corner radius **6 px**, padding roughly `2px 5px`
- vertically centred against a bold ~16 px wordmark, `8px` gap between them

A mark that works *as a knockout inside a green chip* and *as a standalone
coloured glyph* covers every placement in the product.

---

## 5. Locked typefaces

Both are Google Fonts, already loaded by the app.

| Face | Weights in use | Role |
|---|---|---|
| **Instrument Sans** | 400, 500, 600, 700 | All UI text and headings |
| **Spline Sans Mono** | 400, 500, 700 | **All money, percentages, dates and codes** — chosen for tabular figures so amounts align in columns |

For the wordmark, **Instrument Sans 700** is the default starting point — it is
what the current wordmark uses. Custom letterforms drawn *from* it are welcome;
introducing an unrelated third typeface is not.

Spline Sans Mono is the "numbers" voice of the product. If the mark references
digits or currency, that is the face to reference.

Both are open-licensed on Google Fonts (SIL Open Font License), which permits
modification and redistribution — worth confirming independently before a
wordmark is finalised commercially.

---

## 6. Contrast checklist

The mark must hold up on every one of these grounds:

| Ground              | Hex       | Context                  |
| ------------------- | --------- | ------------------------ |
| Light page          | `#F2F4F1` |                          |
| Light card          | `#FFFFFF` | header, login screen     |
| Dark page           | `#101613` |                          |
| Dark card           | `#18211C` | header, login screen     |
| Brand green (light) | `#0E6B4A` | knockout inside the chip |
| Brand green (dark)  | `#4CB98A` | knockout inside the chip |

Also check: greyscale, and a single flat colour with no fills (the mono variant).

---

## 7. Anti-requirements

Things that would actively fight the product:

- **No red or amber in the identity** — reserved for over-budget and near-limit.
- **No gradients or soft shadows.** The UI is flat: solid fills, 1 px borders,
  10–14 px radii. A glossy mark would be the only such object in the product.
- **No pie charts, upward arrows, bull/bear, coins, piggy banks.** Generic
  fintech visual clichés; the product is about *what's left*, not growth or
  investing.
- **No detail that dies below 24 px** — see §4.
- **Nothing that depends on colour alone to be recognisable** — it has to work in
  the mono and greyscale variants.

---

## 8. Useful raw material

Concepts genuinely rooted in this product, offered as starting points rather
than a shortlist:

- **"What's left"** — a partially-filled container, a remainder, a bar not yet
  full. The app's core UI element is a category row whose background fills left
  to right as you spend; the mark could rhyme with that.
- **`RD$`** — the Dominican peso sign, the current placeholder. Literal and
  legible, but ties the brand to one country if the product ever expands.
- **The forwarded message** — everything enters this product as an email that
  gets forwarded. An envelope/arrow idea is available but risks reading as a
  mail client.

---

## 9. Deliverable back into the codebase

Return SVGs with:

- a `viewBox` set tight to the artwork, no surrounding whitespace
- no embedded raster images, no external font references — **convert all text to
  outlines**
- `fill="currentColor"` on the mono variant so it can inherit theme colour
- flat paths, no `<style>` blocks or CSS classes inside the SVG
- for the two-colourway version, either separate files or paths tagged so the
  green can be swapped per theme

Files land in `web/app/static/brand/`. Note this project's repository is
**public**, so do not include licensed font binaries with restrictive
redistribution terms in the deliverables.
