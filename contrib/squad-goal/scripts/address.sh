# usage: . address.sh   (env.sh sources it)   The address seam: which repository an issue, a name or a
# checkout means, the context of that repository, and where an addressed issue lands.
#        bash address.sh <input>   prints the repository context as KEY=value lines (eval-safe); exit 2 = refused
#        bash address.sh --list    prints "<profile>\t<owner/repo>" for every known profile One grammar with one set of readers: a stage that parses an address itself is a
# second dialect waiting to disagree with this one.
# An issue's address is `<owner>/<repo>#<N>`. `sg_address_repo` prints the repository it names -- empty for the legacy bare
# number -- and `sg_address_issue` prints the number.
sg_address_repo() { case "${1:-}" in *'#'*) printf '%s\n' "${1%%#*}";; esac; }
sg_address_issue() { printf '%s\n' "${1##*#}"; }
# A queued comment carries the repository it is for: `<owner>__<repo>#<n>-<tag>.md` in <state>/cq/ (an owner holds no `_`, so the
# first `__` ends it). `sg_cq_file <n>-<tag>.md` prints that path for the repository in force (SG_REPO) and the state directory
# (SG_STATE); every writer of a comment names its file through it, and cqflush.sh reads the repository back from the name.
# It refuses, printing nothing, when no repository is in force or the leaf does not start with an issue number.
sg_cq_file() {
  local leaf=${1:-}
  case "$leaf" in [0-9]*-*) ;; *) echo "sg_cq_file: '$leaf' is not <issue>-<tag>.md" >&2; return 1;; esac
  case "${SG_REPO:-}" in ?*/?*) ;; *) echo "sg_cq_file: no repository in force for $leaf" >&2; return 1;; esac
  printf '%s/cq/%s#%s\n' "${SG_STATE:?sg_cq_file: no state directory}" "${SG_REPO/\//__}" "$leaf"
}
# One loop comment of a kind per issue (#328): `sg_cq_status <n> <tag>` and `sg_cq_delivery <n> <tag>` name a queue file that
# cqflush.sh does not post blindly. It finds the comment carrying the hidden marker of that kind (status or delivery), edits it when the body
# differs, and writes nothing when it is the same. Every writer of a status or a delivery comment names its file through these two.
sg_cq_status() { sg_cq_file "$1-status~$2.md"; }
sg_cq_delivery() { sg_cq_file "$1-delivery~$2.md"; }
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
# The land of an address is the branch landing into the addressed repository's base branch.
# A bare number resolves to the profile repository. A repository outside the profile is refused.
sg_land_target() {
  local n r; n=$(sg_address_issue "${1:-}"); r=$(sg_address_repo "${1:-}")
  [ -n "$r" ] || r=$(sg_resolve_repo "$n" 2>/dev/null) || { echo "refused: no profile repository to resolve #$n" >&2; return 1; }
  sg_same_repo "$r" "${SG_REPO:-}" 2>/dev/null || { echo "refused: no land for '${1:-}' in this profile" >&2; return 1; }
  printf '%s %s %s %s\n' "${SG_CHECKOUT:-}" "${SG_BASE:-main}" - pr
}
# The base branch a repository's land merges into. Same table as sg_land_target, so the two can never disagree.
sg_land_base() { local row; row=$(sg_land_target "${1:-}#0") || return 1; printf '%s\n' "${row#* }" | cut -d' ' -f1; }
# The context of a repository. <input> is a profile slug, a checkout (a path), an address owner/repo#n (owner/repo alone
# too) or a bare n / #n. A bare n is the profile in force (SG_PROFILE) and nothing else: never the profile the cwd's origin
# matches. A repository no profile names is refused by name with the list of known ones; nothing falls back to another.
# The context: SG_PROFILE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_FORK_DIR SG_PROJECTS SG_STATE SG_HUB_PATH SG_ISSUE SG_SHARED SG_BOARD_CACHE SG_SQUAD.
# A profile that still sets the retired SG_TOOLING_REPO is read like any other: env.sh maps it into SG_REPOS and warns.
# A profile is read on its own (no inherited SG_* value reaches it), so one profile can never borrow another's settings.
# SG_PROFILES_DIR (default the profiles/ beside this file) and SG_STATE_BASE (default ~/.cache/squad-goal) are its inputs.
# The context reads nothing from GitHub: it is resolved before any caller may reach it.
_sg_ctx_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SG_CTX_KEYS="SG_PROFILE SG_REPO SG_OWNER SG_PROJECT SG_CHECKOUT SG_BASE SG_FORK_DIR SG_PROJECTS SG_STATE SG_HUB_PATH SG_ISSUE SG_SHARED SG_BOARD_CACHE SG_SQUAD"
# The machine state directory and the places in it. Only these functions spell a path under it.
sg_state_base() { printf '%s\n' "${SG_STATE_BASE:-$HOME/.cache/squad-goal}"; }
# One repository's own directory: its supervisor, cycle, slots, pid files, markers, live-run list, queued requests,
# comment queue, locks, health causes, test artifacts and logs. No repository reads or writes another's.
sg_state_dir() { printf '%s/%s\n' "$(sg_state_base)" "$1"; }
# The one shared directory: the provider limit markers and the provider quota cache. A limit one loop sees skips that model for every loop.
sg_shared_dir() { printf '%s/_shared\n' "$(sg_state_base)"; }
# The board cache: one per owner and project, never one per repository.
sg_board_cache() { printf '%s/_boards/%s_%s/hub-board.json\n' "$(sg_state_base)" "$1" "$2"; }
# The launchd label of one repository's health agent: one agent per repository, so each supervisor is watched.
sg_health_label() { printf 'com.squad-goal.health.%s\n' "$1"; }
sg_profiles_dir() { echo "${SG_PROFILES_DIR:-$_sg_ctx_here/profiles}"; }
# 0 when SG_PROFILE names a profile the profiles directory holds, so a worktree's own prepush.sh --local resolves the
# same profile; 1 otherwise, and the request says the check is unavailable instead of prescribing a command that exits 2.
sg_profile_resolves() { [ -n "${SG_PROFILE:-}" ] && [ -f "$(sg_profiles_dir)/$SG_PROFILE.sh" ]; }
# (#740) The gate commands a request names are the profile's: SG_FAST_GATE_CMD (the entry gate a child runs last),
# SG_PREPUSH_CMD (the prepush check, which prints nothing when clean), SG_SUITES_HINT (the targeted suites a child runs) and
# SG_FULL_GATE_HINT (the full gate the loop keeps). A value the profile sets wins, and an empty one means none. A value the
# profile leaves unset is set empty when the target checkout (SG_CHECKOUT) lacks the script agent-squad's text names, so a
# request never names a script its repository does not hold; else it stays unset, and the request builder writes
# agent-squad's text. env.sh calls this after the profile.
sg_gate_cmds() { local s="${SG_CHECKOUT:-}/contrib/squad-goal/scripts"
  if [ -n "${SG_CHECKOUT:-}" ]; then
    [ -n "${SG_FAST_GATE_CMD+x}" ] || [ -f "$s/fast-gate.sh" ] || SG_FAST_GATE_CMD=
    [ -n "${SG_PREPUSH_CMD+x}" ] || [ -f "$s/prepush.sh" ] || SG_PREPUSH_CMD=
    [ -n "${SG_SUITES_HINT+x}" ] || [ -f "$s/shell-suites.sh" ] || SG_SUITES_HINT=
    [ -n "${SG_FULL_GATE_HINT+x}" ] || [ -f "$s/shell-suites.sh" ] || SG_FULL_GATE_HINT=
  fi
  export SG_FAST_GATE_CMD SG_PREPUSH_CMD SG_SUITES_HINT SG_FULL_GATE_HINT; }
# Every known profile slug, one per line, in byte order; example.sh is a template, never a repository.
sg_profiles() { local f n; for f in "$(sg_profiles_dir)"/*.sh; do [ -f "$f" ] || continue; n=$(basename "$f" .sh)
  [ "$n" = example ] || echo "$n"; done | LC_ALL=C sort; }
# The profile an origin remote names: the profile whose name the remote URL contains, compared lower-case.
sg_profile_from_origin() { local url n hit=""; url=$(git -C "${1:-.}" remote get-url origin 2>/dev/null | tr A-Z a-z) || return 1
  [ -n "$url" ] || return 1
  for n in $(sg_profiles); do case "$url" in *"$(echo "$n" | tr A-Z a-z)"*) hit=$n;; esac; done
  [ -n "$hit" ] && echo "$hit"; }
# sg_profile_parses <file>: 0 when bash parses the profile; else prints "<file> does not parse: line N ..." and returns 1, so
# no script goes on with half a profile (#761).
sg_profile_parses() { local e; e=$(bash -n "$1" 2>&1) && return 0
  echo "repoctx: profile $1 does not parse: $(printf '%s' "$e" | head -1 | sed 's/^[^:]*: //')"; return 1; }
# The context of one profile, KEY=value lines; the profile's own values only.
_sg_ctx_of() { local slug=$1 issue=$2 f out op; f="$(sg_profiles_dir)/$slug.sh"
  [ -f "$f" ] && [ "$slug" != example ] || { echo "repoctx: unknown profile: $slug (known: $(sg_profiles | tr '\n' ' '))" >&2; return 2; }
  sg_profile_parses "$f" >&2 || return 2
  out=$(env $(compgen -e | /usr/bin/grep '^SG_' | /usr/bin/grep -vx 'SG_PROFILES_DIR\|SG_STATE_BASE' | sed 's/^/-u /') \
    bash -c 'here=$1; . "$2" >/dev/null 2>&1
      printf "%s=%q\n" SG_PROFILE "$3" SG_REPO "$SG_REPO" SG_OWNER "${SG_OWNER:-${SG_REPO%%/*}}" SG_PROJECT "$SG_PROJECT" \
        SG_CHECKOUT "$SG_CHECKOUT" SG_BASE "${SG_BASE:-main}" SG_FORK_DIR "$SG_FORK_DIR" SG_PROJECTS "${SG_PROJECTS:-}" SG_HUB_PATH "/$3" SG_ISSUE "$4" SG_SQUAD "${SG_SQUAD:-}"' \
    _ "$_sg_ctx_here" "$f" "$slug" "$issue") || return 2
  op=$(eval "$out"; printf '%s %s' "$SG_OWNER" "$SG_PROJECT")
  printf '%s\n' "$out"
  printf '%s=%q\n' SG_STATE "$(sg_state_dir "$slug")" SG_SHARED "$(sg_shared_dir)" SG_BOARD_CACHE "$(sg_board_cache "${op% *}" "${op#* }")"; }
# sg_profile_setting <slug> <KEY> <default>: the value one profile gives KEY, else <default>; the profile is read on its own, as _sg_ctx_of reads it.
sg_profile_setting() { local f; f="$(sg_profiles_dir)/$1.sh"; [ -f "$f" ] || { echo "$3"; return; }
  env $(compgen -e | /usr/bin/grep '^SG_' | /usr/bin/grep -vx 'SG_PROFILES_DIR\|SG_STATE_BASE' | sed 's/^/-u /') \
    bash -c 'here=$1; . "$2" >/dev/null 2>&1; eval "v=\${$3:-$4}"; echo "$v"' _ "$_sg_ctx_here" "$f" "$2" "$3"; }
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
  if [ "${1:-}" = --list ]; then for n in $(sg_profiles); do f="$(sg_profiles_dir)/$n.sh"
    if e=$(sg_profile_parses "$f"); then printf '%s\t%s\n' "$n" "$(_sg_repo_of "$n")"; else printf '%s\t%s\n' "$n" "${e#repoctx: }"; fi; done; exit 0; fi
  repoctx "${1:-}" || exit 2
fi
