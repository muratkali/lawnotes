#!/bin/bash
# Install or repair Law Notes, a terminal editor for law notes, on this Mac.
#
#   curl -fsSL https://raw.githubusercontent.com/muratkali/lawnotes/main/install.sh | bash
#
# Installs the newest signed release (or LAWNOTES_VERSION=vX.Y.Z): the code in
# ~/.local/share/lawnotes, the `lawnotes` command in ~/.local/bin and Law Notes.app in
# ~/Applications. Notes go in ~/UCL/notes, linked to iCloud Drive/UCL Notes when
# iCloud Drive is on. Releases must be signed with the Law Notes release key below;
# this Mac trusts that key from now on, and update.py refuses anything else.

# The Law Notes release key (public half). Trusted on first install.
RELEASE_KEY='ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIMhskw5ZcDEaxPBGHMu8Dr/3zeTsm1sz0iCuN7ooAsE8 lawnotes-release'

main() {
  set -euo pipefail
  local repo="${LAWNOTES_REPO:-https://github.com/muratkali/lawnotes.git}"
  local root="${LAWNOTES_HOME:-$HOME/.local/share/lawnotes}"
  local signers="${LAWNOTES_SIGNERS:-$HOME/.config/lawnotes/allowed_signers}"

  [ "$(uname)" = Darwin ] || fail "Law Notes is for macOS."
  if ! xcode-select -p >/dev/null 2>&1; then
    echo "Law Notes needs Apple's Command Line Tools (git, Python and Swift)."
    echo "A window will open to install them. Run this installer again when they're done."
    xcode-select --install 2>/dev/null || true
    exit 1
  fi
  python3 -c 'import sys, curses; assert sys.version_info >= (3, 9)' 2>/dev/null \
    || fail "Law Notes needs Python 3.9 or newer with curses (python3 is $(python3 --version 2>&1))."

  # The key releases must be signed with. Kept if already there: it is this Mac's trust anchor.
  if [ ! -s "$signers" ]; then
    mkdir -p "$(dirname "$signers")"
    printf 'lawnotes-release namespaces="git" %s\n' "${RELEASE_KEY% *}" > "$signers"
  fi

  # Version 1.0 installed a plain clone here: move it aside
  if [ -d "$root/.git" ]; then
    local old
    old="$root.old-$(date +%Y%m%d%H%M%S)"
    mv "$root" "$old"
    say "Moved the old Law Notes install to ${old/#$HOME/~} (deleted once this works)"
    trap 'echo "Install failed; the old copy is still in ${old/#$HOME/~}"' ERR
  fi
  mkdir -p "$root"
  if [ ! -d "$root/repo.git" ]; then
    say "Downloading Law Notes"
    git clone -q --bare "$repo" "$root/repo.git"
  fi
  git --git-dir="$root/repo.git" fetch -q origin 'refs/tags/*:refs/tags/*'

  local tag="${LAWNOTES_VERSION:-$(git --git-dir="$root/repo.git" tag -l 'v*' --sort=-v:refname | head -1)}"
  [ -n "$tag" ] || fail "No Law Notes releases found."
  git --git-dir="$root/repo.git" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile="$signers" \
    verify-tag "$tag" >/dev/null 2>&1 || fail "Release $tag isn't signed with the Law Notes release key. Not installing."
  say "Installing Law Notes $tag (signature verified)"

  # Run the verified release's own installer step
  rm -rf "$root/versions/$tag"
  git --git-dir="$root/repo.git" worktree prune
  git --git-dir="$root/repo.git" worktree add -q --detach "$root/versions/$tag" "$tag"
  python3 "$root/versions/$tag/update.py" --install "$tag"
  if [ -n "${old:-}" ]; then rm -rf "$old"; fi
}

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
fail() { echo "$*" >&2; exit 1; }

main "$@"  # whole file is read before running, so a cut-off download runs nothing
