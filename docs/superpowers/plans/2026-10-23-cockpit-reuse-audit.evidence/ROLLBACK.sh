#!/bin/sh
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    printf '%s\n' "usage: $0 TARGET_COPY [BASELINE_FILE]" >&2
    exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
target=$1
baseline=${2:-"$script_dir/CONTINUATION_BASELINE.md"}

if [ ! -f "$baseline" ] || [ ! -f "$target" ]; then
    printf '%s\n' "baseline or target copy is missing" >&2
    exit 1
fi

cp -- "$baseline" "$target"
