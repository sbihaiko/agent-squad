# usage: . address.sh   (env.sh and forkonly.sh source it)   The address seam: which repository an issue, a name or a
# checkout means, the context of that repository, and where an addressed issue lands.
#        bash address.sh <input>   prints the repository context as KEY=value lines (eval-safe); exit 2 = refused
#        bash address.sh --list    prints "<profile>\t<owner/repo>" for every known profile One grammar with one set of readers: a stage that parses an address itself is a
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
# remote. The profile is read by _sg_ctx_of, the reader the context uses. A short name with no owner (`name` or `name#N`) resolves only when exactly one repository the profile works
# carries that name. Anything else is refused: nothing on stdout, the input named on stderr, return 1.
sg_resolve_repo() {
  local a=${1:-} s n hits="" r pd; pd=$(sg_profiles_dir)
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
    r=$(_sg_repo_of "$a")
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
# $SG_FORK_BASE in that checkout, by forkclose.sh. route=pr: the profile's repository ($SG_REPO); its land is the pull
# request's merge into $SG_BASE, by autopilot.sh, so no branch is named.
# A bare number names no repository. `sg_land_target <N> row` says it came WITH a board row: a legacy row, which keeps the
# route it had (local: the branch a bare number was filed with is the fork's), and the line on stderr names it. With no
# `row` the number came with no board row and is the profile in force ($SG_REPO), so it lands on the pr route, and a line
# on stderr says so. A legacy row with no local land to keep is refused, never turned into a pull request in $SG_REPO.
sg_land_target() {
  local n r fr="" row=${2:-}; n=$(sg_address_issue "${1:-}"); r=$(sg_address_repo "${1:-}")
  [ -n "${SG_FORK_DIR:-}" ] && fr=$(sg_resolve_repo "$SG_FORK_DIR" 2>/dev/null)
  if [ -z "$r" ] && [ -n "$row" ]; then
    [ -n "$fr" ] || { echo "refused: legacy row #$n names no repository and this profile has no local land to keep" >&2; return 1; }
    echo "legacy row #$n: the board row names no repository; it keeps route local ($fr, $(sg_fork_branch "$n"))" >&2
  elif [ -z "$r" ]; then
    r=$(sg_resolve_repo "$n" 2>/dev/null) || { echo "refused: no board row for #$n and no profile repository to resolve it to" >&2; return 1; }
    echo "no board row #$n: it resolves to the profile repository $r; route pr" >&2
  fi
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
# The context of a repository. <input> is a profile slug, a checkout (a path), an address owner/repo#n (owner/repo alone
# too) or a bare n / #n. A bare n is the profile in force (SG_PROFILE) and nothing else: never the profile the cwd's origin
# matches. A repository no profile names is refused by name with the list of known ones; nothing falls back to another.
# The context: SG_PROFILE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_FORK_DIR SG_STATE SG_HUB_PATH SG_ISSUE.
# A profile that sets the retired SG_TOOLING_REPO is refused by name: every repository has its own profile.
# A profile is read on its own (no inherited SG_* value reaches it), so one profile can never borrow another's settings.
# SG_PROFILES_DIR (default the profiles/ beside this file) and SG_STATE_BASE (default ~/.cache/squad-goal) are its inputs.
# The context reads nothing from GitHub: it is resolved before any caller may reach it.
_sg_ctx_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SG_CTX_KEYS="SG_PROFILE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_FORK_DIR SG_STATE SG_HUB_PATH SG_ISSUE"
sg_profiles_dir() { echo "${SG_PROFILES_DIR:-$_sg_ctx_here/profiles}"; }
# Every known profile slug, one per line, in byte order; example.sh is a template, never a repository.
sg_profiles() { local f n; for f in "$(sg_profiles_dir)"/*.sh; do [ -f "$f" ] || continue; n=$(basename "$f" .sh)
  [ "$n" = example ] || echo "$n"; done | LC_ALL=C sort; }
# The profile an origin remote names: the profile whose name the remote URL contains, compared lower-case.
sg_profile_from_origin() { local url n hit=""; url=$(git -C "${1:-.}" remote get-url origin 2>/dev/null | tr A-Z a-z) || return 1
  [ -n "$url" ] || return 1
  for n in $(sg_profiles); do case "$url" in *"$(echo "$n" | tr A-Z a-z)"*) hit=$n;; esac; done
  [ -n "$hit" ] && echo "$hit"; }
# The context of one profile, KEY=value lines; the profile's own values only.
_sg_ctx_of() { local slug=$1 issue=$2 f; f="$(sg_profiles_dir)/$slug.sh"
  [ -f "$f" ] && [ "$slug" != example ] || { echo "repoctx: unknown profile: $slug (known: $(sg_profiles | tr '\n' ' '))" >&2; return 2; }
  env $(compgen -e | /usr/bin/grep '^SG_' | /usr/bin/grep -vx 'SG_PROFILES_DIR\|SG_STATE_BASE' | sed 's/^/-u /') \
    bash -c 'here=$1; . "$2" >/dev/null 2>&1
      [ -z "${SG_TOOLING_REPO:-}" ] || { echo "repoctx: profile $3 sets SG_TOOLING_REPO, which is retired: give $SG_TOOLING_REPO its own profile" >&2; exit 2; }
      printf "%s=%q\n" SG_PROFILE "$3" SG_REPO "$SG_REPO" SG_OWNER "${SG_OWNER:-${SG_REPO%%/*}}" SG_PROJECT "$SG_PROJECT" \
        SG_CHECKOUT "$SG_CHECKOUT" SG_BASE "${SG_BASE:-main}" SG_FORK_DIR "$SG_FORK_DIR" \
        SG_STATE "${SG_STATE_BASE:-$HOME/.cache/squad-goal}/$3" SG_HUB_PATH "/$3" SG_ISSUE "$4"' _ "$_sg_ctx_here" "$f" "$slug" "$issue"; }
_sg_repo_of() { _sg_ctx_of "$1" "" 2>/dev/null | sed -n 's/^SG_REPO=//p'; }
_sg_real() { (cd "$1" 2>/dev/null && pwd -P); }
# repoctx <input>: print the context, or refuse (return 2) naming what it could not resolve.
repoctx() { local in=$1 slug="" issue="" n r want co
  case "$in" in
    ""|-*) echo "repoctx: no input (a profile, a checkout, owner/repo#n or n)" >&2; return 2;;
    /*|.*|~*) co=$(_sg_real "${in/#\~/$HOME}") || { echo "repoctx: no such checkout: $in" >&2; return 2; }
      for n in $(sg_profiles); do r=$(_sg_ctx_of "$n" "" | sed -n 's/^SG_CHECKOUT=//p'); r=$(eval "echo $r")
        [ -n "$r" ] && [ "$(_sg_real "$r")" = "$co" ] && { slug=$n; break; }; done
      [ -n "$slug" ] || slug=$(sg_profile_from_origin "$co") || { echo "repoctx: no profile knows the checkout $in" >&2; return 2; };;
    [0-9]*|"#"[0-9]*) issue=${in#\#}; case "$issue" in *[!0-9]*) echo "repoctx: not an issue number: $in" >&2; return 2;; esac
      slug=${SG_PROFILE:-}
      [ -n "$slug" ] || { echo "repoctx: no profile in force for #$issue (set SG_PROFILE, or write owner/repo#$issue)" >&2; return 2; };;
    */*) want=${in%%#*}; [ "$want" != "$in" ] && issue=${in#*#}
      case "$want" in */*/*|/*|*/) echo "repoctx: not an address: $in" >&2; return 2;; esac
      case "$issue" in *[!0-9]*) echo "repoctx: not an issue number in $in" >&2; return 2;; esac
      want=$(echo "$want" | tr A-Z a-z)
      for n in $(sg_profiles); do [ "$(_sg_repo_of "$n" | tr A-Z a-z)" = "$want" ] && { slug=$n; break; }; done
      [ -n "$slug" ] || { echo "repoctx: unknown repository: ${in%%#*} (known: $(for n in $(sg_profiles); do _sg_repo_of "$n"; done | tr '\n' ' '))" >&2; return 2; };;
    *) slug=$in;;
  esac
  _sg_ctx_of "$slug" "$issue"; }
# sg_repo_ctx <input>: the sourced form; sets every context key in the caller's shell, or returns 2 and sets none.
sg_repo_ctx() { local out; out=$(repoctx "$1") || return 2; eval "$out"; }
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
  if [ "${1:-}" = --list ]; then for n in $(sg_profiles); do printf '%s\t%s\n' "$n" "$(_sg_repo_of "$n")"; done; exit 0; fi
  repoctx "${1:-}" || exit 2
fi
