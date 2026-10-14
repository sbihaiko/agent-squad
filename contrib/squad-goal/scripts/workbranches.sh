# usage: . "$here/workbranches.sh"   then: sg_work_branches <remote> || { echo "PASS: $wb_err"; exit 0; }
# The work branches a branch pass acts on (branchland.sh, branchreviewpass.sh): one `<sha> <branch> <issue>` line per branch in
# $wb_list. A work branch is `fix/<n>-…` or `gui/<n>-…` on the remote and names issue <n>. A branch with an open pull request is
# left to the PR lander, and a branch whose issue is not open is old work (#367): neither is listed.
# The branch list is `git ls-remote` (no API read); the open-PR and open-issue lists are one read each, through ghcall.sh (gh_api).
# A read that fails returns 1 with the reason in $wb_err. $wb_issues keeps the open-issue list (REST shape, with labels[].name).
sg_work_branches() { local remote=$1 heads pl prb oi openiss sha ref br i
  wb_list=; wb_err=; wb_issues=
  heads=$(/usr/bin/git ls-remote --heads "$remote" 2>&1) || { wb_err="the remote branch list is not readable: $(printf '%s' "$heads" | tail -1)"; return 1; }
  pl=$(gh_api "repos/$SG_REPO/pulls?per_page=100") || { wb_err="the open-PR list is not readable: $pl"; return 1; }
  printf '%s' "$pl" | jq -e 'type=="array"' >/dev/null 2>&1 || { wb_err="the open-PR list is not a list"; return 1; }
  prb=" $(printf '%s' "$pl" | jq -r '.[].head.ref' | tr '\n' ' ') "
  oi=$(gh_api "repos/$SG_REPO/issues?state=open") || { wb_err="the open-issue list is not readable: $oi"; return 1; }
  openiss=" $(printf '%s' "$oi" | jq -r '.[].number' 2>/dev/null | tr '\n' ' ') "; wb_issues=$oi
  while read -r sha ref; do
    br=${ref#refs/heads/}
    i=$(printf '%s' "$br" | sed -nE 's#^(fix|gui)/([0-9]+)([^0-9].*)?$#\2#p'); [ -n "$i" ] || continue
    case "$prb" in *" $br "*) continue;; esac
    case "$openiss" in *" $i "*) ;; *) continue;; esac
    wb_list+="$sha $br $i"$'\n'
  done <<EOT
$(printf '%s\n' "$heads" | awk '{print $1, $2}')
EOT
  return 0; }
