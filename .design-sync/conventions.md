# Cualto — how to build with this design system

Cualto is a personal budget tracker for the Dominican Republic. It parses bank
card-notification emails and shows what's left to spend this month. Screens are
mobile-first PWA pages, bilingual (Spanish default, English toggle).

**This is a plain-CSS design system — no React, no component library.** There is
no bundle to import and no props API. You style with the semantic class names and
custom properties below, exactly as the real app does. Do not introduce utility
classes (`p-4`, `text-sm`), CSS-in-JS, or a framework — none exist here.

## Setup

Two requirements, both on the root element:

```html
<html data-theme="light">  <!-- or "dark" — REQUIRED, tokens resolve per theme -->
  <head><link rel="stylesheet" href="styles.css"></head>
  <body>
    <main> ... your page ... </main>
  </body>
</html>
```

Without `data-theme` on `<html>` you get the light tokens by default; `dark`
must be set explicitly on that element (not a wrapper div) or dark designs
render with light colors. `<main>` carries the page frame: `max-width: 940px`,
centered, safe-area padding. Put content inside it.

## Tokens — the entire palette

Twelve properties in `tokens/tokens.css`, each with a light and dark value.
Always reference them as `var(--ink)`, `var(--brand)` and so on — never a raw hex.

`--bg` page · `--card` raised surface · `--line` borders · `--ink` primary text ·
`--muted` secondary text · `--btn-ink` text on brand fill · `--brand` accent ·
`--good` under budget · `--warn` near limit · `--bad` over budget / destructive ·
`--font-ui` (Instrument Sans) · `--font-mono` (Spline Sans Mono)

For tinted fills there are no extra tokens — the idiom is
`color-mix(in srgb, var(--warn) 10%, transparent)`. Follow it.

Note `--good` and `--brand` are currently the same value in both themes.

## Money must use `.num`

Every amount, percentage, date, and code gets `class="num"` — that's
`--font-mono` plus `font-variant-numeric: tabular-nums`, so figures align in
columns. This is a money app; unaligned amounts are a real defect. Pair with
`.pos` / `.neg` for sign color.

## Class vocabulary

- **Layout** — `.card` (rounded raised panel, the default container),
  `.section-title` (uppercase muted label above a group), `.grid`, `.grid.cols`
  (5fr/4fr above 960px), `.prose` for legal/long-form text.
- **Buttons** — a bare `<button>` is **already the filled brand button**; there
  is no `.primary` class, so do not add one. Variants: `.quiet` (borderless
  secondary), `.small-btn` (bordered neutral), `.danger-btn` (filled `--bad`),
  `.block` (full width, for auth forms). For links styled as buttons use
  `.cta-btn`, inside a `.cta` panel.
- **Budget display** — `.hero` wraps `.eyebrow` + `.amount` + `.sub` (the big
  "what's left" number). `.cats` > `.cat` is a category row whose background
  *is* the meter: an absolutely-positioned `.fill` with an inline `width: N%`
  (a soft tint, plus a solid rule on its **bottom** edge for the exact
  figure — never a vertical edge, which would cross the text), plus
  `.cat-line`, `.cat-name`, `.cat-left`, `.cat-meta`. Add `.warn` or `.bad`
  to `.cat` to shift its `--c`. `.stat-row` > `.stat` > `.stat-n` + `.stat-l`
  for counters.
- **Transactions** — `.txns` > `.txn` > `.txn-line` (`.txn-merchant` + amount),
  then `.txn-meta` for the category/card/date line.
- **Forms** — `.field` (label above input) for stacked forms, `.field-row` for
  inline ones, `input.amount` for right-aligned monospace money inputs,
  `.alert.err` for errors, `.switch` > `input` + `.track` for toggles.
- **Status** — `.pill` (warn-tinted, for uncategorized prompts), `.chip` /
  `.chip.active` (filters), `.status-pill` with `.processed` / `.unrecognized` /
  `.failed`.
- **Chrome** — `header` > `.brand` (`.brand-mark` + `.brand-text`), `nav` with
  `aria-current="page"` on the active link, `.user-menu`, `.lang-toggle`,
  `.theme-toggle`.
- **Responsive nav** — `nav` and `.user-menu` sit inside `.hdr-menu`, which is
  `display: contents` on desktop (the wrapper dissolves; the bar lays out as a
  flat row). **At or below 900px it becomes a slide-in drawer** with a
  `.nav-scrim` behind it and a `.nav-close` button inside it, opened by
  `.nav-toggle`. The open state is the class `nav-open` on `header`, and the
  whole mechanism only applies when `header` also has `.has-nav` — which is
  rendered only for signed-in users. Do not design a wide horizontal nav for
  phone widths; it will not be what ships.

## Read these before styling

`styles.css` and its two imports — `tokens/tokens.css` and `_ds_bundle.css` —
are the real app stylesheet, extracted verbatim. They are authoritative; this
summary is not. The preview cards under `components/` show every class above
rendered in both themes.

## Two constraints that break layouts

1. **Spanish runs ~20% longer than English** and is the default. Buttons and nav
   items must tolerate it. On desktop `nav` scrolls horizontally rather than
   wrapping; at phone widths it is the drawer described above, so long labels
   are not a problem there.
2. **The header must survive 320px.** The bar carries `.nav-toggle`, the full
   `.brand` lockup (`.brand-mark` + `.brand-text`, both visible at every width)
   and `.theme-toggle` — signed out, the `.lang-toggle` instead of the
   hamburger. Nothing else belongs in it; everything else goes in the drawer.

## Example

```html
<div class="card hero">
  <p class="eyebrow">Te queda este mes</p>
  <p class="amount">RD$ 12,480<small>.50</small></p>
  <p class="sub">de RD$ 45,000 presupuestados</p>
</div>

<div class="card">
  <p class="section-title">Por categoría</p>
  <ul class="cats">
    <li class="cat warn">
      <span class="fill" style="width:84%"></span>
      <div>
        <div class="cat-line"><span class="cat-name">Transporte</span>
          <span class="cat-left num">RD$ 960</span></div>
        <div class="cat-meta"><span>5,040 / 6,000</span><span>84%</span></div>
      </div>
    </li>
  </ul>
</div>
```
