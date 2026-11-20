# usage: . "$here/workbranches.sh"   then: sg_work_branches <remote> || { echo "PASS: $wb_err"; exit 0; }
# The work branches a branch pass acts on (branchland.sh, branchreviewpass.sh): one `<sha> <branch> <issue>` line per branch in
# $wb_list. A work branch is `fix/<n>-…` or `gui/<n>-…` on the remote and names issue <n>. A branch whose issue is not open is old
# work (#367): it is not listed. The branch list is `git ls-remote` (no API read); the open-issue list is one read through
# ghcall.sh (gh_api). A read that fails returns 1 with the reason in $wb_err. $wb_issues keeps the open-issue list (REST shape,
# with labels[].name). $wb_heads keeps every branch name the remote listed, one per line, whatever its issue (#2571).
# A dropped connect is transient and lives outside this repository, so the branch list read is tried again inside the same pass:
# at most SG_BRANCH_LIST_TRIES reads (default 3), SG_BRANCH_LIST_PAUSE seconds apart (default 1). Only a dropped connect is
# retried; any other failure fails the read at once. A read that keeps failing returns 1 with its one reason in $wb_err, and the
# pass that calls it lands nothing and starts no fix round. SG_GIT names the git binary (default /usr/bin/git).
sg_work_branches() { local remote=$1 heads oi openiss sha ref br i gitb=${SG_GIT:-/usr/bin/git} tries=${SG_BRANCH_LIST_TRIES:-3} try=1 got=
  wb_list=; wb_err=; wb_issues=; wb_heads=
  while :; do
    heads=$("$gitb" ls-remote --heads "$remote" 2>&1) && { got=1; break; }
    printf '%s' "$heads" | /usr/bin/grep -qiE 'failed to connect|could not resolve host|connection (reset|refused|timed out)|network is unreachable|early eof|rpc failed|remote end hung up|temporary failure in name resolution' || break
    [ "$try" -lt "$tries" ] || break
    try=$((try+1)); sleep "${SG_BRANCH_LIST_PAUSE:-1}"
  done
  [ -n "$got" ] || { wb_err="the remote branch list is not readable: $(printf '%s' "$heads" | tail -1)"; return 1; }
  wb_heads=$(printf '%s\n' "$heads" | awk '{print $2}' | sed -n 's#^refs/heads/##p')
  oi=$(gh_api "repos/$SG_REPO/issues?state=open") || { wb_err="the open-issue list is not readable: $oi"; return 1; }
  openiss=" $(printf '%s' "$oi" | jq -r '.[].number' 2>/dev/null | tr '\n' ' ') "; wb_issues=$oi
  while read -r sha ref; do
    br=${ref#refs/heads/}
    i=$(printf '%s' "$br" | sed -nE 's#^(fix|gui)/([0-9]+)([^0-9].*)?$#\2#p'); [ -n "$i" ] || continue
    case "$openiss" in *" $i "*) ;; *) continue;; esac
    wb_list+="$sha $br $i"$'\n'
  done <<EOT
$(printf '%s\n' "$heads" | awk '{print $1, $2}')
EOT
  return 0; }

# sg_work_issues <remote>: the issue numbers an open work branch names, one space between them and a trailing space, or "" when
# none. It is the frontier's "work is in flight" fact and the same fact autobug and health read (#361): a work branch is the unit
# of work now, so an issue with one is past dispatch. `git ls-remote` only, no GitHub call.
sg_work_issues() { local remote=${1:-https://github.com/$SG_REPO.git} heads
  heads=$(/usr/bin/git ls-remote --heads "$remote" 2>/dev/null) || return 0
  printf '%s\n' "$heads" | awk '{print $2}' | sed -nE 's#^refs/heads/(fix|gui)/([0-9]+)([^0-9].*)?$#\2#p' | sort -nu | tr '\n' ' '; }
