#!/bin/bash
# Publish a Law Notes release: ./release.sh 1.2.0
#
# Checks that main is pushed, VERSION and CHANGELOG match, and CI passed for this exact
# commit; then signs the tag with the release key, pushes it and creates the GitHub
# release.
#
# Signing needs you: the release key lives in 1Password, whose SSH agent asks you to approve
# each signature (Touch ID), so nothing else on this Mac can sign a release. Without 1Password,
# unlock the key in the system ssh-agent first:
#   ssh-add -t 900 ~/.ssh/lawnotes_release_ed25519     # asks for the passphrase; 15 minutes
#
# Every Mac picks a release up within a day (after verifying the signature and running
# the self-test), or straight away with `lawnotes --update`.
set -euo pipefail

key="${LAWNOTES_RELEASE_KEY:-$HOME/.ssh/lawnotes_release_ed25519}"
repo=muratkali/lawnotes
fail() { echo "release: $*" >&2; exit 1; }
push() { git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push "$@"; }

[ $# -eq 1 ] || fail "usage: ./release.sh X.Y.Z"
version="$1"; tag="v$version"
cd "$(dirname "$0")"

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "version must look like 1.2.0"
[ -f "$key.pub" ] || fail "release key's public half not found at $key.pub"
[ -z "$(git status --porcelain)" ] || fail "commit or stash your changes first"
[ "$(git branch --show-current)" = main ] || fail "release from main"
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || fail "push main first (git push)"
grep -q "^VERSION = \"$version\"" lawnotes.py || fail "set VERSION = \"$version\" in lawnotes.py"
grep -q "^## $version" CHANGELOG.md || fail "add a '## $version' section to CHANGELOG.md"
! git rev-parse -q --verify "refs/tags/$tag" >/dev/null || fail "$tag already exists"

sha=$(git rev-parse HEAD)
# GitHub sometimes starts the same workflow twice for one push and one copy can hang without a
# runner: a commit counts as tested once one complete CI run on it has passed.
ci=$(gh run list --repo "$repo" --commit "$sha" --workflow CI --json status,conclusion | python3 -c '
import json, sys
runs = json.load(sys.stdin)
done = [r for r in runs if r["status"] == "completed"]
print("success" if any(r["conclusion"] == "success" for r in done)
      else "failure" if any(r["conclusion"] not in ("cancelled", "skipped") for r in done)
      else "pending" if runs else "none")')
[ "$ci" = success ] || fail "CI is '$ci' for $sha; wait for it to pass (gh run watch)"

# Which ssh-agent can sign: 1Password's (asks you to approve with Touch ID) or one you unlocked
onepassword="$HOME/Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock"
pub=$(cut -d' ' -f1-2 "$key.pub")
agent=""
for sock in "${LAWNOTES_SIGN_AGENT:-}" "$onepassword" "${SSH_AUTH_SOCK:-}"; do
  [ -n "$sock" ] && [ -S "$sock" ] || continue
  if SSH_AUTH_SOCK="$sock" ssh-add -L 2>/dev/null | grep -qF "$pub"; then agent="$sock"; break; fi
done
[ -n "$agent" ] || fail "the release key isn't available: add it to 1Password, or ssh-add -t 900 $key"
export SSH_AUTH_SOCK="$agent"
[ "$agent" = "$onepassword" ] && echo "Signing with 1Password: approve the request (Touch ID)."

signers=$(mktemp)
trap 'rm -f "$signers"' EXIT
printf 'lawnotes-release namespaces="git" %s\n' "$(cut -d' ' -f1-2 "$key.pub")" > "$signers"
git -c gpg.format=ssh -c user.signingkey="$key.pub" tag -s "$tag" -m "Law Notes $version"  # signs via the agent
git -c gpg.format=ssh -c gpg.ssh.allowedSignersFile="$signers" verify-tag "$tag"
push origin "$tag"

notes=$(awk -v v="## $version" '$0 == v || index($0, v " ") == 1 {on=1; next} /^## / {on=0} on' CHANGELOG.md)
gh release create "$tag" --repo "$repo" --title "Law Notes $version" --notes "$notes" --verify-tag
echo "Released $tag. Update this Mac now with: lawnotes --update"
