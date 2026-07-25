#!/usr/bin/env python3
"""Regenerate the design-system CSS from the app's own stylesheet.

The app keeps all of its CSS in a single <style> block in base.html. The Claude
Design bundle needs that split in two — tokens (the :root custom-property
blocks) and everything else — so a rendered design can import the real class
vocabulary rather than a reimplementation of it.

Run after any change to base.html's <style> block:

    python3 scripts/build_ds_bundle.py

Outputs (never hand-edit):
    ds-bundle/tokens/tokens.css
    ds-bundle/_ds_bundle.css
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "web" / "app" / "templates" / "base.html"
OUT = ROOT / "ds-bundle"

TOKENS_HEADER = """\
/* Design tokens — extracted verbatim from web/app/templates/base.html
   by scripts/build_ds_bundle.py. Single source of truth for color and type.
   Do not fork these values; edit base.html and re-run the script. */

"""

BUNDLE_HEADER = """\
/* Component styles — extracted verbatim from web/app/templates/base.html
   by scripts/build_ds_bundle.py. The app's real class vocabulary, not a
   reimplementation. Edit base.html and re-run the script. */

"""


def split_style_block(html: str) -> tuple[str, str]:
    """Return (token css, component css) from base.html's single <style> block."""
    m = re.search(r"^\s*<style>\n(.*?)^\s*</style>", html, re.S | re.M)
    if not m:
        sys.exit("base.html: could not find a <style> ... </style> block")
    block = m.group(1)

    # The token blocks are the leading `:root { ... }` / `:root[data-theme=...] { ... }`
    # rules. Everything from the first non-:root rule onward is component CSS.
    parts = re.split(r"\n(?=\s*\*\s*\{)", block, maxsplit=1)
    if len(parts) != 2:
        sys.exit("base.html: expected the token blocks to be followed by `* { ... }`")
    tokens, rest = parts

    if "--brand:" not in tokens or "--bad:" not in tokens:
        sys.exit("base.html: token block does not define the expected custom properties")
    if "--brand:" in rest or "--bg:" in rest:
        sys.exit("base.html: token definitions leaked into the component CSS")

    dedent = lambda s: re.sub(r"^  ", "", s, flags=re.M)
    return dedent(tokens).strip() + "\n", dedent(rest).strip() + "\n"


FONTS = (
    '@import url("https://fonts.googleapis.com/css2?family=Instrument+Sans'
    ':wght@400;500;600;700&family=Spline+Sans+Mono:wght@400;500;700&display=swap");'
)

INJECT_START = "<!-- ds:inject-start -->"
INJECT_END = "<!-- ds:inject-end -->"


def rescope_for_preview(tokens: str, bundle: str) -> str:
    """Rewrite the app CSS so light and dark can sit side by side in one page.

    The app scopes its themes to the document root, which only ever has one
    theme at a time. A preview card shows both, so `:root` becomes `.t` and the
    dark variant becomes `.t.dark`. Everything else is left untouched — that is
    the point: the class rules the cards demonstrate are byte-identical to the
    ones the app ships.
    """
    css = tokens + "\n" + bundle

    # Theme carriers: :root -> .t, :root[data-theme="dark"] -> .t.dark
    css = css.replace(':root[data-theme="dark"] .sun', ".t.dark .sun")
    css = css.replace(':root[data-theme="light"] .moon', ".t:not(.dark) .moon")
    css = css.replace(':root[data-theme="dark"]', ".t.dark")
    css = re.sub(r"^:root\b", ".t", css, flags=re.M)

    # `body` sets the page surface and base font; in a card that is the pane.
    css = re.sub(r"^body\s*\{", ".t {", css, flags=re.M)

    return FONTS + "\n\n" + css


def inject_previews(tokens: str, bundle: str) -> list[str]:
    """Fill each preview card's generated-CSS region from the real stylesheet."""
    block = rescope_for_preview(tokens, bundle)
    touched = []
    for f in sorted((OUT / "components").rglob("*.html")):
        html = f.read_text()
        if INJECT_START not in html:
            sys.exit(f"{f}: missing {INJECT_START} marker")
        head, rest = html.split(INJECT_START, 1)
        _, tail = rest.split(INJECT_END, 1)
        f.write_text(
            f"{head}{INJECT_START}\n"
            "<!-- GENERATED from web/app/templates/base.html — do not edit by hand. -->\n"
            f"<style>\n{block}\n</style>\n{INJECT_END}{tail}"
        )
        touched.append(f.parent.name)
    return touched


def main() -> None:
    tokens, bundle = split_style_block(BASE.read_text())
    (OUT / "tokens").mkdir(parents=True, exist_ok=True)
    (OUT / "tokens" / "tokens.css").write_text(TOKENS_HEADER + tokens)
    (OUT / "_ds_bundle.css").write_text(BUNDLE_HEADER + bundle)
    for p in (OUT / "tokens" / "tokens.css", OUT / "_ds_bundle.css"):
        print(f"{p.relative_to(ROOT)}  {len(p.read_text().splitlines())} lines")
    print("previews refreshed:", ", ".join(inject_previews(tokens, bundle)))


if __name__ == "__main__":
    main()
