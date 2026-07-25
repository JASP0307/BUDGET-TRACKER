#!/usr/bin/env bash
# Back up the files that are NOT in git and cannot be regenerated.
#
#   scripts/backup-secrets.sh [--dry-run]
#
# WHY THIS EXISTS
# Everything in this repo is public and re-clonable. These files are not, and
# losing them means losing access rather than losing code:
#
#   config.toml              Google/Telegram credentials, budget, categorize
#                            rules, and [cards.labels] — the ONLY place the real
#                            card last4 digits live now that they are out of the
#                            repo. These labels feed the Sheet dedupe signature,
#                            so losing them means every historical charge gets
#                            re-logged on the next run.
#   credentials.json         Gmail OAuth client
#   token.json               cached Gmail/Sheets OAuth token
#   ~/.config/budget-web.env session secret, Fernet key, webhook secrets
#   web/dev.db               the web app's users, transactions and budgets
#
# The Fernet key matters twice over: without it, the encrypted email bodies in
# dev.db are unreadable, so key and database have to be backed up together.
#
# The archive is written OUTSIDE the repo (~/backups/budget-tracker) so it can
# never be committed by accident, and chmod 600 because it is a bundle of
# credentials in plaintext.
#
# HONEST LIMITATION: a copy on the same disk protects against a bad edit or an
# `rm`, not against that disk dying. Copy the newest archive somewhere else
# periodically — that is the part this script cannot do for you.
#
# Optional encryption: set BACKUP_PASSPHRASE_FILE to a file containing a
# passphrase and the archive is gpg-encrypted (.tar.gz.gpg). Worth it only if the
# archive leaves this machine; a passphrase stored beside the archive protects
# nothing locally.
#
# Install (crontab, daily at 04:00):
#   0 4 * * * [ -x <repo>/scripts/backup-secrets.sh ] && cd <repo> && scripts/backup-secrets.sh >> <repo>/backup.log 2>&1
set -uo pipefail
export PATH="/usr/bin:/bin:/usr/sbin:/sbin:$PATH"
cd "$(dirname "$0")/.."
REPO="$PWD"

DEST="${BACKUP_DEST:-$HOME/backups/budget-tracker}"
KEEP="${BACKUP_KEEP:-14}"
ENVF="${WEB_ENV_FILE:-$HOME/.config/budget-web.env}"
DRY_RUN=""
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

say() { echo "$(date '+%F %T') $*"; }

# Paths are relative to the repo except the env file, which is absolute.
ITEMS=(config.toml credentials.json token.json web/dev.db)

present=()
for f in "${ITEMS[@]}"; do
  if [ -e "$REPO/$f" ]; then present+=("$f"); else say "note: $f absent, skipping"; fi
done
[ -e "$ENVF" ] || say "note: $ENVF absent, skipping"

if [ ${#present[@]} -eq 0 ] && [ ! -e "$ENVF" ]; then
  say "ERROR: nothing to back up — wrong directory?" >&2
  exit 1
fi

stamp=$(date +%F-%H%M%S)
archive="$DEST/budget-tracker-secrets-$stamp.tar.gz"

if [ -n "$DRY_RUN" ]; then
  say "would archive to $archive:"
  for f in "${present[@]}"; do echo "    $f"; done
  [ -e "$ENVF" ] && echo "    $(basename "$ENVF")"
  exit 0
fi

mkdir -p "$DEST"
chmod 700 "$DEST"

# Stage the env file under a predictable name so restores are obvious.
staging=$(mktemp -d)
trap 'rm -rf "$staging"' EXIT
[ -e "$ENVF" ] && cp -p "$ENVF" "$staging/$(basename "$ENVF")"

if ! tar -czf "$archive" -C "$REPO" "${present[@]}" \
     ${ENVF:+-C "$staging" "$(basename "$ENVF")"} 2>/dev/null; then
  say "ERROR: tar failed" >&2
  rm -f "$archive"
  exit 1
fi
chmod 600 "$archive"

# Verify rather than trust: every expected member must be in the archive.
# List once into a variable and match with a herestring — piping `tar` into
# `grep -q` makes grep exit on the first match, SIGPIPEs tar, and under
# `pipefail` that reads as a failed pipeline even when the file was found.
listing=$(tar -tzf "$archive") || { say "ERROR: cannot read back $archive" >&2; exit 1; }
missing=0
for f in "${present[@]}" "$(basename "$ENVF")"; do
  grep -qx "$f" <<<"$listing" || { say "ERROR: $f missing from archive" >&2; missing=1; }
done
if [ "$missing" != "0" ]; then
  say "ERROR: archive incomplete, keeping it for inspection: $archive" >&2
  exit 1
fi

say "wrote $archive ($(du -h "$archive" | cut -f1), $(tar -tzf "$archive" | wc -l) files)"

# Retention: keep the newest $KEEP archives.
mapfile -t old < <(ls -1t "$DEST"/budget-tracker-secrets-*.tar.gz 2>/dev/null | tail -n +$((KEEP + 1)))
for f in "${old[@]:-}"; do
  [ -n "$f" ] && { rm -f "$f"; say "pruned $(basename "$f")"; }
done

# Optional encryption for archives destined to leave this machine.
if [ -n "${BACKUP_PASSPHRASE_FILE:-}" ] && [ -f "$BACKUP_PASSPHRASE_FILE" ]; then
  gpg --batch --yes --quiet --symmetric --cipher-algo AES256 \
      --passphrase-file "$BACKUP_PASSPHRASE_FILE" "$archive" \
    && { chmod 600 "$archive.gpg"; rm -f "$archive"; say "encrypted -> $archive.gpg"; }
fi

say "reminder: copy the newest archive off this machine — same-disk backups do not survive the disk"
