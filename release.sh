#!/bin/bash
# Publish a Law Notes release: ./release.sh 1.2.0
#
# Checks that main is pushed, VERSION and CHANGELOG match, and CI passed for this exact
# commit; then signs the tag with the release key, pushes it and creates the GitHub
# release. Every Mac picks it up within a day (after verifying the signature and
# running the self-test), or straight away with `lawnotes --update`.
set -euo pipefail

key="${LAWNOTES_RELEASE_KEY:-$HOME/.ssh/lawnotes_release_ed25519}"
repo=muratkali/lawnotes
fail() { echo "release: $*" >&2; exit 1; }
push() { git -c credential.helper= -c 'credential.helper=!gh auth git-credential' push "$@"; }

[ $# -eq 1 ] || fail "usage: ./release.sh X.Y.Z"
version="$1"; tag="v$version"
cd "$(dirname "$0")"

[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "version must look like 1.2.0"
[ -f "$key" ] || fail "release key not found at $key"
[ -z "$(git status --porcelain)" ] || fail "commit or stash your changes first"
[ "$(git branch --show-current)" = main ] || fail "release from main"
git fetch -q origin
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || fail "push main first (git push)"
grep -q "^VERSION = \"$version\"" lawnotes.py || fail "set VERSION = \"$version\" in lawnotes.py"
grep -q "^## $version" CHANGELOG.md || fail "add a '## $version' section to CHANGELOG.md"
! git rev-parse -q --verify "refs/tags/$tag" >/dev/null || fail "$tag already exists"

sha=$(git rev-parse HEAD)
ci=$(gh api "repos/$repo/commits/$sha/check-runs" | python3 -c '
import json, sys
runs = json.load(sys.stdin)["check_runs"]
print("none" if not runs else "pending" if any(r["status"] != "completed" for r in runs)
      else "success" if all(r["conclusion"] in ("success", "skipped") for r in runs) else "failure")')
[ "$ci" = success ] || fail "CI is '$ci' for $sha; wait for it to pass (gh run watch)"

signers=$(mktemp)
trap 'rm -f "$signers"' EXIT
printf 'lawnotes-release namespaces="git" %s\n' "$(cut -d' ' -f1-2 "$key.pub")" > "$signers"
git -c gpg.format=ssh -c user.signingkey="$key" tag -s "$tag" -m "Law Notes $version"
git -c gpg.format=ssh -c gpg.ssh.allowedSignersFile="$signers" verify-tag "$tag"
push origin "$tag"

notes=$(awk -v v="## $version" '$0 == v || index($0, v " ") == 1 {on=1; next} /^## / {on=0} on' CHANGELOG.md)
gh release create "$tag" --repo "$repo" --title "Law Notes $version" --notes "$notes" --verify-tag
echo "Released $tag. Update this Mac now with: lawnotes --update"
