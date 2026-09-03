#!/usr/bin/env sh
# Run the tree-sitter CLI without a global install.
#
# Downloads the release binary into the project cache on first use and keeps
# compiled parsers there too. Usage:
#
#   cd tree-sitter-reword
#   ../scripts/tree-sitter.sh generate
#   ../scripts/tree-sitter.sh test
#   ../scripts/tree-sitter.sh parse ../tests/data/example.reword
set -eu

version=0.25.4
root=$(cd "$(dirname "$0")/.." && pwd)
cache="$root/.cache"
bin="$cache/tree-sitter-$version"

case "$(uname -s)-$(uname -m)" in
  Linux-x86_64) asset=tree-sitter-linux-x64 ;;
  Linux-aarch64) asset=tree-sitter-linux-arm64 ;;
  Darwin-arm64) asset=tree-sitter-macos-arm64 ;;
  Darwin-x86_64) asset=tree-sitter-macos-x64 ;;
  *) echo "unsupported platform: $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

mkdir -p "$cache/tree-sitter-lib"
if [ ! -x "$bin" ]; then
  url="https://github.com/tree-sitter/tree-sitter/releases/download/v$version/$asset.gz"
  echo "Downloading $url" >&2
  curl -fsSL "$url" | gunzip > "$bin.tmp"
  chmod +x "$bin.tmp"
  mv "$bin.tmp" "$bin"
fi

export TREE_SITTER_LIBDIR="$cache/tree-sitter-lib"
# The CLI also keeps a lock and config under XDG dirs; keep those in-project.
export XDG_CACHE_HOME="$cache/xdg"
export XDG_CONFIG_HOME="$cache/xdg"
exec "$bin" "$@"
