#!/bin/bash
# Install or update Law Notes, a terminal editor for law notes, on this Mac.
#
#   curl -fsSL https://raw.githubusercontent.com/muratkali/lawnotes/main/install.sh | bash
#
# Puts the code in ~/.local/share/lawnotes (a git clone, so it can update itself),
# the `lawnotes` command in ~/.local/bin, and Law Notes.app in ~/Applications.
# Notes go in ~/UCL/notes, linked to iCloud Drive/UCL Notes when iCloud Drive is on.
# Running it again updates everything; `lawnotes --update` does the same.

main() {
  set -euo pipefail
  local repo="${LAWNOTES_REPO:-https://github.com/muratkali/lawnotes.git}"
  local home="${LAWNOTES_HOME:-$HOME/.local/share/lawnotes}"
  local bin="$HOME/.local/bin"
  local app="$HOME/Applications/Law Notes.app"
  local notes="$HOME/UCL/notes"
  local icloud="$HOME/Library/Mobile Documents/com~apple~CloudDocs"

  [ "$(uname)" = Darwin ] || { echo "Law Notes is for macOS."; exit 1; }
  if ! xcode-select -p >/dev/null 2>&1; then
    echo "Law Notes needs Apple's Command Line Tools (git, Python and Swift)."
    echo "A window will open to install them. Run this installer again when they're done."
    xcode-select --install 2>/dev/null || true
    exit 1
  fi

  # 1. The code: a git clone, so updates are a fast-forward
  if [ -d "$home/.git" ]; then
    say "Updating Law Notes in ${home/#$HOME/~}"
    git -C "$home" pull --ff-only -q
  else
    say "Downloading Law Notes to ${home/#$HOME/~}"
    mkdir -p "$(dirname "$home")"
    git clone -q "$repo" "$home"
  fi
  chmod +x "$home/lawnotes" "$home/lawnotes.py" "$home/app/Law Notes.command"

  # 2. The `lawnotes` command
  mkdir -p "$bin"
  ln -sfn "$home/lawnotes" "$bin/lawnotes"
  if ! grep -qs '\.local/bin' "$HOME/.zprofile" "$HOME/.zshrc"; then
    printf '\n# Added by the Law Notes installer\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zprofile"
    say "Added ~/.local/bin to your PATH (in ~/.zprofile): new Terminal windows will know \`lawnotes\`"
  fi

  # 3. Law Notes.app (rebuilt each time; only replaces an app that is ours)
  if [ -e "$app" ] && ! grep -qs 'li.muratka.lawnotes' "$app/Contents/Info.plist"; then
    say "Skipping the app: ${app/#$HOME/~} exists and isn't Law Notes"
  else
    say "Building ${app/#$HOME/~}"
    rm -rf "$app"
    mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"
    cp "$home/app/Info.plist" "$app/Contents/Info.plist"
    cp "$home/app/AppIcon.icns" "$app/Contents/Resources/AppIcon.icns"
    printf '#!/bin/bash\nexec "%s/lawnotes" --app\n' "$home" > "$app/Contents/MacOS/law-notes"
    chmod +x "$app/Contents/MacOS/law-notes"
    codesign --force --deep --sign - "$app" >/dev/null 2>&1 || true
    /System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister \
      -f "$app" >/dev/null 2>&1 || true
  fi

  # 4. The notes folder (never touched if it already exists)
  if [ ! -e "$notes" ]; then
    mkdir -p "$(dirname "$notes")"
    if [ -d "$icloud" ]; then
      mkdir -p "$icloud/UCL Notes"  # iCloud merges this with the same folder from your other Macs
      ln -s "$icloud/UCL Notes" "$notes"
      say "Notes: ~/UCL/notes → iCloud Drive/UCL Notes"
    else
      mkdir -p "$notes"
      say "Notes: ~/UCL/notes (iCloud Drive is off, so notes stay on this Mac)"
    fi
  fi

  say "Done: $(python3 "$home/lawnotes.py" --version)"
  echo "    Open it from Spotlight (⌘Space → Law Notes) or type: lawnotes"
}

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }

main "$@"  # whole file is read before running, so updating it mid-run is safe
