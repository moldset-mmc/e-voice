#!/bin/sh
# Install one complete release in the current user account.
set -eu
version=1.1.2
release="https://github.com/moldset-mmc/e-voice/releases/download/v$version"
system=$(uname -s)
machine=$(uname -m)
case "$system/$machine" in
  Darwin/arm64|Darwin/x86_64) name="E-Voice-macOS-$machine.pkg" ;;
  Linux/x86_64) name=E-Voice-Linux-amd64.tar.gz ;;
  Linux/aarch64|Linux/arm64) name=E-Voice-Linux-arm64.tar.gz ;;
  *) echo "No published package for $system/$machine." >&2; exit 1 ;;
esac
tmp=$(mktemp -d)
stage=''
cleanup() {
  if [ -n "$stage" ]; then
    case "$stage" in "$base"/.install-*) rm -rf -- "$stage" ;; esac
  fi
  rm -f "$tmp/$name" "$tmp/SHA256SUMS.txt"
  rmdir "$tmp"
}
trap cleanup EXIT
from=''
case $# in
  0) ;;
  2) [ "$1" = --from ] || exit 2; from=$2 ;;
  *) echo 'Usage: install.sh [--from directory]' >&2; exit 2 ;;
esac
fetch() {
  if [ -n "$from" ]; then
    cp "$from/$1" "$tmp/$1"
  else
    curl -fLsS --retry 3 "$release/$1" -o "$tmp/$1"
  fi
}
fetch SHA256SUMS.txt
expected=$(awk -v file="$name" '$2 == file {print $1}' "$tmp/SHA256SUMS.txt")
[ "${#expected}" -eq 64 ] || { echo 'Missing release checksum.' >&2; exit 1; }
fetch "$name"
if [ "$system" = Darwin ]; then
  actual=$(shasum -a 256 "$tmp/$name" | cut -d ' ' -f 1)
else
  actual=$(sha256sum "$tmp/$name" | cut -d ' ' -f 1)
fi
[ "$actual" = "$expected" ] || { echo 'Incorrect checksum.' >&2; exit 1; }
if [ "$system" = Darwin ]; then
  /usr/sbin/installer -pkg "$tmp/$name" -target CurrentUserHomeDirectory
  echo 'Open E Voice from ~/Applications.'
  exit 0
fi
base="$HOME/.local/opt/e-voice"
bin="$HOME/.local/bin"
desktop="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$base" "$bin" "$desktop"
target="$base/$version-$machine"
if [ ! -e "$target" ]; then
  stage=$(mktemp -d "$base/.install-XXXXXX")
  # Only the verified release archive is unpacked, into a new, unused directory.
  if tar -xzf "$tmp/$name" -C "$stage" && [ -x "$stage/EVoice/EVoice" ]; then
    printf '%s\n' "$expected" > "$stage/EVoice/.release-sha256"
    mv -T "$stage/EVoice" "$target" || [ -d "$target" ]
  else
    echo 'Extraction failed.' >&2
    exit 1
  fi
fi
[ -x "$target/EVoice" ] && [ "$(cat "$target/.release-sha256")" = "$expected" ] || {
  echo "The existing installation in $target needs repair." >&2; exit 1;
}
if [ -e "$bin/e-voice" ] && [ ! -L "$bin/e-voice" ]; then
  echo "Another executable already exists at $bin/e-voice." >&2; exit 1
fi
ln -sfnT "$target/EVoice" "$bin/e-voice"
# Desktop entry Exec quoting is independent of shell quoting.
launch=$(printf '%s' "$bin/e-voice" | sed 's/[\\"$`]/\\\\&/g; s/%/%%/g')
desktop_tmp=$(mktemp "$desktop/.e-voice-XXXXXX")
printf '[Desktop Entry]\nType=Application\nName=E Voice\nExec="%s"\nIcon=%s\nTerminal=false\nCategories=AudioVideo;Audio;Accessibility;\n' \
  "$launch" "$target/_internal/e-voice.png" > "$desktop_tmp"
chmod 644 "$desktop_tmp"
mv -f "$desktop_tmp" "$desktop/e-voice.desktop"
echo 'E Voice is installed. Open it from the application menu.'
