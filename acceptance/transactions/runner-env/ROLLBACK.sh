#!/bin/sh
set -eu

if [ "$#" -ne 1 ]; then
  printf '%s\n' 'usage: ROLLBACK.sh TARGET_COPY' >&2
  exit 2
fi

target=$1
if [ ! -f "$target" ]; then
  printf '%s\n' 'target copy must exist' >&2
  exit 2
fi

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
baseline="$script_dir/BASELINE.py"
if [ ! -f "$baseline" ]; then
  printf '%s\n' 'sibling BASELINE.py must exist' >&2
  exit 2
fi

cat "$baseline" > "$target"
