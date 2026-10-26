#!/bin/sh
# usage: setup.sh <bare-repo> <commit> <dest>   -- full-tree snapshot + submodules at gitlink commits (no history)
set -e
R=$1; C=$2; D=$3
rm -rf "$D"; mkdir -p "$D"
git -C "$R" archive --format=tar "$C" | tar -x -C "$D"
git -C "$R" ls-tree -r "$C" | awk '$1=="160000"{print $3, $4}' | while read sha path; do
  url=$(git -C "$R" show "$C:.gitmodules" | awk -v p="$path" '$1=="path"&&$3==p{f=1} f&&$1=="url"{print $3; exit}')
  slug=$(echo "$url" | sed -E 's#^https?://github.com/##; s#\.git$##; s#/$##')
  B=/tmp/claude-0/repos/$(echo "$slug" | tr '/' '_').git
  [ -d "$B" ] || git clone -q --bare --filter=blob:none "https://github.com/$slug" "$B"
  mkdir -p "$D/$path"; git -C "$B" archive --format=tar "$sha" | tar -x -C "$D/$path"
  echo "submodule $path <- $slug@$sha"
done
