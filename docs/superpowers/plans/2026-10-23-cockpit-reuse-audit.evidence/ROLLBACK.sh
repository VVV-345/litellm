#!/bin/sh
set -eu

# LOCAL ONLY. This script never talks to a server and does not prove image or
# database rollback. Server rollback still needs a recorded release tag, image
# digest, database backup and an operator-approved maintenance window.

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "${1:-}" = "--restore-file" ]; then
    if [ "$#" -ne 3 ] || [ ! -f "$2" ] || [ ! -f "$3" ]; then
        printf '%s\n' "usage: $0 --restore-file TARGET_COPY BASELINE_FILE" >&2
        exit 2
    fi
    cp -- "$3" "$2"
    exit 0
fi

repo_root=$(git -C "$script_dir" rev-parse --show-toplevel)
patch="$script_dir/DIFF_FILE.patch"
base_commit="1a6902d443ae8209a80a5be8eb3c26ede0f06e16"
mode=${1:---check}

case "$mode" in
    --check|--revert-worktree) ;;
    *)
        printf '%s
' "usage: $0 [--check|--revert-worktree]" >&2
        exit 2
        ;;
esac

if [ -n "$(git -C "$repo_root" status --porcelain)" ]; then
    printf '%s
' "worktree is dirty; commit or stash before rollback checks" >&2
    exit 1
fi

current=$(git -C "$repo_root" rev-parse HEAD)
if ! git -C "$repo_root" merge-base --is-ancestor "$base_commit" "$current"; then
    printf '%s
' "HEAD $current is not a descendant of the patch base $base_commit" >&2
    exit 1
fi

git -C "$repo_root" apply --check -R "$patch"
git -C "$repo_root" diff --check "$base_commit..HEAD" -- . ":(exclude)docs/superpowers/plans/2026-10-23-cockpit-reuse-audit.evidence"

if [ "$mode" = "--check" ]; then
    printf '%s
' "local reverse-apply check passed for $current; no files changed"
    printf '%s
' "server rollback is NOT verified by this script"
    exit 0
fi

git -C "$repo_root" apply -R "$patch"
printf '%s
' "local worktree changes reverted; commits are unchanged"
printf '%s
' "server rollback is still NOT verified by this script"
