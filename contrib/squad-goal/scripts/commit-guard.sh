#!/bin/bash
# usage: commit-guard.sh [--install | <path>...]
#
# The commit guard of ADR-0177: a repository never holds a `slack.local.yaml`. A file whose name ends in `slack.local.yaml`
# carries what cannot go into a public repository (a client's Slack workspace and the user ids of each role), so a commit
# that stages one is refused. The name is checked, never the content: the file never enters a tree at all.
#
# With no argument it reads the files a commit stages, so it runs as a `pre-commit` hook: `--install` writes that hook into
# the current repository's hooks directory. An existing hook this guard did not write is refused, never overwritten, and a
# second `--install` changes nothing. Named paths are checked as given, without git.
#
# Prints one `- slack.local: <path> ...` line per refused path. Exit 0 clean, 1 a refused path, 2 usage.
here="$(cd "$(dirname "$0")" && pwd)"
G=/usr/bin/git
suffix=slack.local.yaml
marker="# commit-guard.sh (ADR-0177)"
refused=()
takes() { case "$(basename "$1")" in *"$suffix") refused+=("$1");; esac; }

case "${1:-}" in
  --install)
    [ $# -eq 1 ] || { echo "usage: commit-guard.sh [--install | <path>...]" >&2; exit 2; }
    hook=$($G rev-parse --git-path hooks)/pre-commit
    if [ -e "$hook" ] && ! grep -qF "$marker" "$hook" 2>/dev/null; then
      echo "commit-guard: $hook already exists and was not written by this guard; move it first" >&2; exit 1
    fi
    mkdir -p "$(dirname "$hook")" || exit 1
    printf '%s\n#!/bin/bash\nexec bash %q\n' "$marker" "$here/commit-guard.sh" > "$hook" && chmod +x "$hook" || exit 1
    echo "commit-guard: installed $hook"
    exit 0;;
  '') ;;                                   # the staged files of this repository
  *)  for p in "$@"; do takes "$p"; done;;  # the named paths
esac
if [ -z "${1:-}" ]; then
  $G rev-parse --git-dir >/dev/null 2>&1 || { echo "commit-guard: not inside a repository; name the paths to check" >&2; exit 2; }
  while IFS= read -r -d '' p; do takes "$p"; done < <($G diff --cached --name-only --diff-filter=ACMR -z)
fi
[ ${#refused[@]} -eq 0 ] && exit 0
printf -- '- slack.local: %s is a local Slack file; it stays outside every repository\n' "${refused[@]}"; exit 1
