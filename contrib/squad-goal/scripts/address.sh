# usage: . address.sh   (forkonly.sh sources it)   The address seam: which repository an issue, a name or a checkout means,
# and where an addressed issue lands. One grammar with one set of readers: a stage that parses an address itself is a
# second dialect waiting to disagree with this one.
# An issue's address is `<owner>/<repo>#<N>`. `sg_address_repo` prints the repository it names -- empty for the legacy bare
# number -- and `sg_address_issue` prints the number.
sg_address_repo() { case "${1:-}" in *'#'*) printf '%s\n' "${1%%#*}";; esac; }
sg_address_issue() { printf '%s\n' "${1##*#}"; }
# The repositories the profile works, one per line, the primary first, each once. A profile names them in SG_REPOS. A
# profile that still names the retired SG_TOOLING_REPO has that repository read as one more entry after SG_REPO.
sg_repos() {
  local r seen=" "
  for r in ${SG_REPOS:-} ${SG_REPO:-} ${SG_TOOLING_REPO:-}; do
    case "$seen" in *" $r "*) continue;; esac
    seen="$seen$r "; printf '%s\n' "$r"
  done
}
# The one resolver: `owner/repo#N` (or `owner/repo`), a bare `N` or `#N`, a profile name, or a checkout directory -> one
# `owner/name` on stdout. A bare number is the profile's own repository. A profile name is the repository that profile
# names (profiles in $SG_PROFILES_DIR, default the profiles/ beside this file). A checkout is the repository of its origin
# remote. A short name with no owner (`name` or `name#N`) resolves only when exactly one repository the profile works
# carries that name. Anything else is refused: nothing on stdout, the input named on stderr, return 1.
sg_resolve_repo() {
  local a=${1:-} s n hits="" r pd=${SG_PROFILES_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/profiles}
  [ -n "$a" ] || { echo "refused: an empty name is no repository" >&2; return 1; }
  if [ -d "$a" ]; then
    r=$(git -C "$a" remote get-url origin 2>/dev/null | sed -E 's#\.git$##; s#^.*[:/]([^/:]+/[^/]+)$#\1#')
    case "$r" in */*) printf '%s\n' "$r"; return 0;; esac
    echo "refused: checkout '$a' has no origin remote that names owner/name" >&2; return 1
  fi
  case "$a" in
    */*#*|*/*) r=${a%%#*}; case "$r" in ?*/?*) printf '%s\n' "$r"; return 0;; esac;;
    \#*|[0-9]*) case "${a#\#}" in *[!0-9]*) ;; *) [ -n "${SG_REPO:-}" ] && { printf '%s\n' "$SG_REPO"; return 0; };; esac;;
  esac
  if [ -f "$pd/$a.sh" ]; then
    r=$( unset SG_REPO SG_REPOS; here=$pd; . "$pd/$a.sh" >/dev/null 2>&1; set -- ${SG_REPOS:-} ${SG_REPO:-}; printf '%s' "${1:-}" )
    [ -n "$r" ] && { printf '%s\n' "$r"; return 0; }
    echo "refused: profile '$a' names no repository" >&2; return 1
  fi
  s=${a%%#*}; n=0
  case "$s" in ''|*/*|*[!A-Za-z0-9._-]*) echo "refused: '$a' is no address, profile or checkout" >&2; return 1;; esac
  for r in $(sg_repos); do [ "${r##*/}" = "$s" ] && { hits="$hits $r"; n=$((n+1)); }; done
  [ "$n" = 1 ] && { printf '%s\n' "${hits# }"; return 0; }
  [ "$n" = 0 ] && echo "refused: short name '$a' matches no repository this profile works ($(sg_repos | tr '\n' ' '))" >&2 \
    || echo "refused: short name '$a' matches more than one repository:$hits; write owner/name" >&2
  return 1
}
# The same repository only when owner AND name match. A short name is resolved first (sg_resolve_repo), so `other/tool`
# is never `owner/tool`, and a short name that resolves to no single repository is refused.
sg_same_repo() {
  local a b; [ -n "${1:-}" ] && [ -n "${2:-}" ] || return 1
  a=$(sg_resolve_repo "$1") && b=$(sg_resolve_repo "$2") || return 1
  [ "$a" = "$b" ]
}
# The LAND of an address: the checkout, the base, the branch it merges and the route that owns it, resolved in the
# repository the address NAMES and in no other. Merging is the one step a revert does not undo cheaply, so a repository
# this profile does not land is REFUSED (nothing printed, the address named on stderr, return 1), never guessed.
# route=local: the repository the fork checkout ($SG_FORK_DIR) resolves to; its branch (sg_fork_branch) is merged into
# $SG_FORK_BASE in that checkout, by forkclose.sh. A bare legacy number reads this row: the branch a bare number was filed
# with is the fork's. route=pr: the profile's repository ($SG_REPO); its land is the pull request's merge into $SG_BASE,
# by autopilot.sh, so no branch is named.
sg_land_target() {
  local n r fr=""; n=$(sg_address_issue "${1:-}"); r=$(sg_address_repo "${1:-}")
  [ -n "${SG_FORK_DIR:-}" ] && fr=$(sg_resolve_repo "$SG_FORK_DIR" 2>/dev/null)
  if [ -n "$fr" ] && { [ -z "$r" ] || sg_same_repo "$r" "$fr" 2>/dev/null; }; then
    printf '%s %s %s %s\n' "$SG_FORK_DIR" "${SG_FORK_BASE:-main}" "$(sg_fork_branch "$n")" local
  elif [ -n "$r" ] && sg_same_repo "$r" "${SG_REPO:-}" 2>/dev/null; then
    printf '%s %s %s %s\n' "${SG_CHECKOUT:-}" "${SG_BASE:-main}" - pr
  else
    echo "refused: no land for '${1:-}' in this profile" >&2; return 1
  fi
}
# The base branch a repository's land merges into. Same table as sg_land_target, so the two can never disagree.
sg_land_base() { local row; row=$(sg_land_target "${1:-}#0") || return 1; printf '%s\n' "${row#* }" | cut -d' ' -f1; }
