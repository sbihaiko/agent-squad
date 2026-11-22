#!/bin/bash
# usage: install-hooks.sh [<repository>]
#
# The tracked step that arms the commit guard of ADR-0177 in a repository. `squad setup --apply` runs it, and a person
# runs it by hand after a clone. It installs the guard's `pre-commit` hook in the repository at the named path, or at the
# repository that holds the current directory. The guard keeps the rules: a foreign hook is refused, never overwritten,
# and a second run changes nothing.
#
# Exit 0 when the hook is in place, otherwise the guard's own exit code. The guard is found beside this script, so the
# hook points at this checkout.
here="$(cd "$(dirname "$0")" && pwd)"
repo=${1:-$(pwd)}
cd "$repo" || { echo "install-hooks: no directory $repo" >&2; exit 2; }
/usr/bin/git rev-parse --git-dir >/dev/null 2>&1 || { echo "install-hooks: $repo is not a repository; nothing to arm" >&2; exit 2; }
exec bash "$here"/commit-guard.sh --install
