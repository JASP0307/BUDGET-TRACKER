# design-sync notes

Findings and corrections from syncing this repo to claude.ai/design.
Read before the next sync.

## This repo is an off-script sync

`package-build.mjs` and `resync.mjs` **cannot run here**. The skill's converter
expects a JS component library with a `dist/`; this repo is Python/FastAPI with
Jinja templates and a single inline `<style>` block in `base.html`. There is no
`package.json`, no `*.tsx`, no Storybook, no bundle to compile.

Consequences, all deliberate:

- **No `_ds_bundle.js`, no `.d.ts`, no `.jsx`, no `_vendor/`.** Nothing to
  compile means the design agent cannot instantiate our components. It styles
  from `conventions.md` + the real CSS instead. This is a styles-only design
  system.
- **No `_ds_sync.json`.** The hash recipe (`renderHashFor`, `sourceKeyFor`)
  keys off artifacts we don't produce. Per the skill, omitting the sidecar is
  the honest choice: the next sync re-verifies everything rather than trusting
  an anchor that vouches for nothing.
- **`shape: "package"` in config.json is nominal** — it's the closer of the two
  and keeps the field populated, but no package-shape script is ever invoked.

## How the bundle is produced

`scripts/build_ds_bundle.py` is the converter. It splits the `<style>` block in
`web/app/templates/base.html` into `ds-bundle/tokens/tokens.css` (the `:root`
custom-property blocks) and `ds-bundle/_ds_bundle.css` (everything else), then
injects a theme-rescoped copy into each preview card between the
`<!-- ds:inject-start -->` / `<!-- ds:inject-end -->` markers.

**Re-run it after any change to `base.html`'s style block.** The generated
regions are marked "do not edit by hand" and rerunning is idempotent.

Rescoping: previews show light and dark side by side, which the app never does,
so `:root` → `.t` and `:root[data-theme="dark"]` → `.t.dark`. Everything else is
byte-identical to what the app ships — that is the point. Preview-only chrome
(`.panes`, `.pane-label`, `.spec`, `.demo-*`, `.swatch`) lives in a second
`<style>` after the generated one.

## Bugs this sync caught

- **Previews originally hand-copied the CSS and drifted.** The copy omitted
  `a { color: inherit; }` (base.html:45), so the wordmark and active nav link
  rendered browser-blue. Fixed by generating the CSS instead of copying it —
  do not reintroduce hand-written copies of app CSS into previews.
- **`Buttons.html` invented a `.primary` class.** The app has no such class; a
  bare `<button>` is already the filled brand button. Removed.
- **`ColorTokens.html` used `.chip` for swatches**, colliding with the app's
  real filter-chip class once the real CSS was injected. Renamed to `.swatch`.
  Preview-only class names must not collide with app class names.

## Open issues in the app itself (not sync problems)

- **`.cat .fill`'s `border-right: 2px` cuts through the amount text** at high
  fill percentages — visible at 84% and 125% in the CategoryRow card, and at
  low percentages it cuts the category name (8% slices "Salud"). This is a real
  defect in production, not a preview artifact. Left unfixed: out of scope for
  the sync, and a redesign candidate.
- **`--good` and `--brand` are the same value** in both themes (`#0E6B4A` /
  `#4CB98A`), so "under budget" is indistinguishable from brand accent. Flagged
  in the ColorTokens and CategoryRow cards.
- **Fonts load from the Google Fonts CDN**, not self-hosted. The CSP hardening
  item in TASKS.md depends on self-hosting them; when that lands, add a
  `fonts/` dir to the bundle and change the `@import` in `styles.css`.

## Environment

- No system browser. Preview verification used Playwright Chromium installed
  into a scratchpad venv (`playwright install chromium` — the headless-shell
  download fails in this sandbox, so launch with `channel="chromium"`).
- `document.fonts.check()` returns unreliable results here; it reported webfonts
  missing on cards where they had plainly loaded. Verify visually, not with it.
